from __future__ import annotations

import os
import shutil
import subprocess
import asyncio
import time
import socket
from dataclasses import replace
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, urlopen

from .artifacts import annotate_artifact, annotate_artifacts
from .models import ExecutionResult, Plan, TaskSpec, action_fingerprint_from_plan, plan_fingerprint, utc_now
from .policy import approval_required as task_approval_required, draft_only_for_goal, infer_risk
from .profiles import ProfileStore
from .proxy import playwright_proxy_settings, proxy_dict_for_requests, resolve_proxy_url
from .providers import PROVIDERS
from .redaction import redact, redact_headers, redact_text, safe_json_dumps
from .router import provider_sequence_constraint_failures, target_scope_for_url
from .brightdata.browser import BrightDataBrowserError, scrape_with_browser
from .brightdata.client import BrightDataError
from .brightdata.datasets import scrape_dataset_url, search_dataset
from .brightdata.serp import search as brightdata_search
from .brightdata.unlocker import unlock_url
from .brightdata.zones import missing_env_for_lane


SENSITIVE_TARGET_SCOPES = {"loopback", "private_network", "link_local", "local_file"}
BROWSER_SCOPED_REQUEST_SCHEMES = {"http", "https", "file"}
PROVIDER_TRANSPORT_HTTP_SCHEMES = {"http", "https"}
PROVIDER_TRANSPORT_CDP_SCHEMES = {"http", "https", "ws", "wss"}
ALLOW_INTERNAL_PROVIDER_BASES_ENV = "SUPER_BROWSER_ALLOW_INTERNAL_PROVIDER_BASES"
ALLOW_INSECURE_PROVIDER_BASES_ENV = "SUPER_BROWSER_ALLOW_INSECURE_PROVIDER_BASES"
FAILED_PROVIDER_STATUSES = {"failed", "failure", "error", "errored", "cancelled", "canceled", "timeout", "timed_out", "expired", "stopped"}
UNFINISHED_PROVIDER_STATUSES = {"pending", "queued", "running", "processing", "in_progress", "started"}
SUCCESS_PROVIDER_STATUSES = {"complete", "completed", "done", "finished", "success", "succeeded"}


class ProviderAdapter(Protocol):
    name: str

    def execute(self, plan: Plan, run_id: str, artifact_dir: Path) -> ExecutionResult:
        ...


def _task_proxy_url(task: TaskSpec) -> str | None:
    return resolve_proxy_url(task, fleet_index=task.fleet_index)


def _provider_profile_ref(task: TaskSpec, provider: str) -> str | None:
    if not task.profile:
        return None
    bound = ProfileStore(create=False).resolve_provider_id(task.profile, provider)
    return bound or task.profile


def _browser_use_api_base() -> str:
    return os.environ.get("BROWSER_USE_API_BASE", "https://api.browser-use.com").rstrip("/")


def _browser_use_profile_id(task: TaskSpec) -> str | None:
    if not task.profile:
        return None
    store = ProfileStore(create=False)
    bound = store.resolve_provider_id(task.profile, "browser-use")
    if bound:
        return bound
    api_key = os.environ.get("BROWSER_USE_API_KEY")
    if not api_key:
        return None
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        payload = _http_json(
            f"{_browser_use_api_base()}/v3/profiles",
            {"name": task.profile},
            headers,
            timeout_seconds=30,
        )
        profile_id = str(payload.get("id") or payload.get("profile_id") or "")
        if profile_id:
            ProfileStore().bind_provider_id(task.profile, "browser-use", profile_id)
            return profile_id
    except Exception:
        return None
    return None


def _airtop_session_configuration(task: TaskSpec) -> dict[str, Any]:
    configuration: dict[str, Any] = {"timeoutMinutes": int(os.environ.get("AIRTOP_TIMEOUT_MINUTES", "5"))}
    if task.profile:
        configuration["profileName"] = task.profile
    return configuration


def _hyperbrowser_session_options(task: TaskSpec) -> dict[str, Any]:
    proxy_url = _task_proxy_url(task)
    options: dict[str, Any] = {
        "useStealth": bool(task.anti_bot_risk),
        "useProxy": bool(os.environ.get("HYPERBROWSER_USE_PROXY") or proxy_url or task.proxy),
    }
    if task.profile:
        options["profile"] = _provider_profile_ref(task, "hyperbrowser")
    if proxy_url:
        options["proxy"] = proxy_url
    return options


def _steel_session_body(task: TaskSpec) -> dict[str, Any]:
    body: dict[str, Any] = {"timeout": _timeout_seconds(task, 300) * 1000}
    if task.profile:
        body["persistProfile"] = True
        profile_ref = _provider_profile_ref(task, "steel")
        if profile_ref:
            body["profileId"] = profile_ref
    proxy_url = _task_proxy_url(task)
    if proxy_url:
        body["proxy"] = proxy_url
    return body


def execute_plan(
    plan: Plan,
    run_id: str,
    state_dir: Path | None = None,
    use_fallbacks: bool = True,
    approval_granted: bool = False,
    approval_context: dict[str, Any] | None = None,
) -> ExecutionResult:
    artifact_dir = (state_dir or Path(".super-browser")) / "artifacts" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    provider_constraint_failures = provider_sequence_constraint_failures(plan)
    if provider_constraint_failures:
        return _provider_constraints_result(plan, run_id, artifact_dir, provider_constraint_failures)
    approval_check = _approval_context_check(plan, approval_context, approval_granted=approval_granted)
    if not approval_check["valid"]:
        return _approval_required_result(plan, run_id, artifact_dir, approval_check["reason"])
    provider_sequence = _provider_sequence(plan) if use_fallbacks else [plan.primary_provider]
    attempts = []
    all_events = []
    all_artifacts = []
    last_result: ExecutionResult | None = None
    for index, provider_name in enumerate(provider_sequence, start=1):
        attempt_dir = artifact_dir / f"attempt-{index:02d}-{_safe_provider_name(provider_name)}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        provider_plan = _plan_for_provider(plan, provider_name)
        all_events.append(_event("attempt_started", provider_name))
        try:
            result = get_adapter(provider_name).execute(provider_plan, run_id, attempt_dir)
        except Exception as exc:
            result = _provider_exception_result(provider_name, run_id, attempt_dir, exc)
        last_result = result
        all_events.extend(result.events)
        all_artifacts.extend(result.artifacts)
        attempts.append(
            {
                "order": index,
                "provider": provider_name,
                "status": result.status,
                "error": redact_text(result.error),
                "artifact_count": len(result.artifacts),
                "verification": redact(result.verification),
            }
        )
        all_events.append(_event(f"attempt_{result.status}", provider_name))
        if result.status == "complete":
            return _finalize_sequence_result(run_id, artifact_dir, plan, result, attempts, all_artifacts, all_events)

    if last_result is None:
        last_result = ExecutionResult(provider=plan.primary_provider, status="blocked", error="No provider attempts were available.")
    return _finalize_sequence_result(run_id, artifact_dir, plan, last_result, attempts, all_artifacts, all_events)


def _approval_required_result(plan: Plan, run_id: str, artifact_dir: Path, reason: str | None = None) -> ExecutionResult:
    reason = reason or "durable approval is required before provider execution"
    result = ExecutionResult(
        provider=plan.primary_provider,
        status="blocked",
        error=f"Provider execution requires durable approval context. {reason}. Use create_run plus approve/resume so runtime can pass structured approval proof.",
        events=[_event("blocked", "approval_required")],
        verification={
            "confidence": "high",
            "checks": [
                "approval-gated plan was not executed",
                reason,
            ],
        },
    )
    return _finalize_sequence_result(run_id, artifact_dir, plan, result, [], [], result.events)


def _provider_constraints_result(plan: Plan, run_id: str, artifact_dir: Path, failures: list[dict[str, Any]]) -> ExecutionResult:
    reason = "; ".join(failure.get("message", failure.get("type", "provider constraint failed")) for failure in failures)
    result = ExecutionResult(
        provider=plan.primary_provider,
        status="blocked",
        error=f"Provider execution violates task constraints. {reason}. Replan the run instead of dispatching this provider sequence.",
        events=[_event("blocked", "provider_constraints")],
        verification={
            "confidence": "high",
            "checks": [
                "provider sequence constraints were enforced before execution",
                reason,
            ],
            "failures": failures,
        },
    )
    return _finalize_sequence_result(run_id, artifact_dir, plan, result, [], [], result.events)


def _provider_exception_result(provider_name: str, run_id: str, artifact_dir: Path, exc: Exception) -> ExecutionResult:
    error_type = exc.__class__.__name__
    error_message = redact_text(str(exc)) or ""
    error = f"{provider_name} adapter raised {error_type}"
    if error_message:
        error = f"{error}: {error_message}"
    meta_path = artifact_dir / "provider-exception.json"
    meta_path.write_text(
        _redacted_json_dump(
            {
                "run_id": run_id,
                "provider": provider_name,
                "error_type": error_type,
                "error": error_message,
            }
        ),
        encoding="utf-8",
    )
    return ExecutionResult(
        provider=provider_name,
        status="failed",
        error=error,
        artifacts=[{"type": "metadata", "path": str(meta_path), "provider": provider_name, "reason": "provider_exception"}],
        events=[_event("failed", "provider_exception")],
        verification={
            "confidence": "low",
            "checks": [
                "provider adapter exception was captured",
                f"{provider_name} failed before returning an execution result",
            ],
            "failures": [
                {
                    "type": "provider_exception",
                    "provider": provider_name,
                    "error_type": error_type,
                    "message": error_message,
                }
            ],
        },
    )


def _plan_requires_approval(plan: Plan) -> bool:
    return bool(plan.approval_required or task_approval_required(plan.task))


def _approval_context_check(plan: Plan, approval_context: dict[str, Any] | None, approval_granted: bool = False) -> dict[str, Any]:
    if not _plan_requires_approval(plan):
        return {"valid": True, "reason": "approval is not required"}
    if approval_granted and not approval_context:
        return {"valid": False, "reason": "bare approval_granted=True is not sufficient without approval_context"}
    if not isinstance(approval_context, dict):
        return {"valid": False, "reason": "structured approval_context is missing"}
    if approval_context.get("status") != "approved":
        return {"valid": False, "reason": "approval_context status is not approved"}
    if not approval_context.get("approval_id"):
        return {"valid": False, "reason": "approval_context approval_id is missing"}
    if not approval_context.get("decided_at") or not approval_context.get("decided_by"):
        return {"valid": False, "reason": "approval_context decision metadata is missing"}
    if approval_context.get("required_before") not in {"provider_execution", "provider_retry"}:
        return {"valid": False, "reason": "approval_context required_before is invalid"}
    if approval_context.get("action_fingerprint") != action_fingerprint_from_plan(plan):
        return {"valid": False, "reason": "approval_context action fingerprint does not match plan"}
    if approval_context.get("plan_sha256") != plan_fingerprint(plan):
        return {"valid": False, "reason": "approval_context plan fingerprint does not match plan"}
    return {"valid": True, "reason": "approval_context verified"}


def get_adapter(provider_name: str) -> ProviderAdapter:
    if provider_name == "playwright":
        return PlaywrightAdapter()
    if provider_name == "decodo-http":
        return RawHttpAdapter()
    if provider_name == "browser-use":
        return BrowserUseAdapter()
    if provider_name == "orgo":
        return OrgoAdapter()
    if provider_name == "airtop":
        return AirtopAdapter()
    if provider_name == "hyperbrowser":
        return HyperbrowserAdapter()
    if provider_name == "steel":
        return SteelAdapter()
    if provider_name == "brightdata-unlocker":
        return BrightDataUnlockerAdapter()
    if provider_name == "brightdata-serp":
        return BrightDataSerpAdapter()
    if provider_name == "brightdata-dataset":
        return BrightDataDatasetAdapter()
    if provider_name == "brightdata-browser":
        return BrightDataBrowserAdapter()
    return ExternalProviderAdapter(provider_name)


def _provider_sequence(plan: Plan) -> list[str]:
    sequence = [plan.primary_provider, *plan.fallback_providers]
    seen = set()
    deduped = []
    for provider_name in sequence:
        if provider_name in PROVIDERS and provider_name not in seen:
            seen.add(provider_name)
            deduped.append(provider_name)
    return deduped


def _plan_for_provider(plan: Plan, provider_name: str) -> Plan:
    return replace(plan, primary_provider=provider_name, fallback_providers=[name for name in plan.fallback_providers if name != provider_name])


def _finalize_sequence_result(
    run_id: str,
    artifact_dir: Path,
    plan: Plan,
    final_result: ExecutionResult,
    attempts: list[dict],
    artifacts: list[dict],
    events: list[dict],
) -> ExecutionResult:
    report_path = artifact_dir / "run-report.json"
    artifacts = annotate_artifacts(artifacts)
    report = {
        "run_id": run_id,
        "plan_sha256": plan_fingerprint(plan),
        "primary_provider": plan.primary_provider,
        "fallback_providers": plan.fallback_providers,
        "final_provider": final_result.provider,
        "final_status": final_result.status,
        "final_error": final_result.error,
        "attempts": attempts,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "cost_estimate": plan.cost_estimate,
        "verification": final_result.verification,
    }
    report_path.write_text(_redacted_json_dump(report), encoding="utf-8")
    artifacts.append(annotate_artifact({"type": "run_report", "path": str(report_path), "provider": final_result.provider, "attempts": len(attempts)}))
    verification = dict(redact(final_result.verification))
    checks = list(verification.get("checks", []))
    checks.append(f"provider attempts: {len(attempts)}")
    if final_result.provider != plan.primary_provider:
        checks.append(f"fallback selected: {final_result.provider}")
    verification.update(
        {
            "confidence": verification.get("confidence", "low"),
            "checks": checks,
            "selected_provider": final_result.provider,
            "attempts": attempts,
            "run_report": str(report_path),
            "cost_estimate": plan.cost_estimate,
        }
    )
    error = final_result.error
    if final_result.status != "complete" and attempts:
        error = "All provider attempts stopped: " + "; ".join(
            f"{attempt['provider']}={attempt['status']}" + (f" ({attempt['error']})" if attempt.get("error") else "") for attempt in attempts
        )
    return ExecutionResult(
        provider=final_result.provider,
        status=final_result.status,
        artifacts=redact(artifacts),
        events=redact(events),
        verification=redact(verification),
        error=redact_text(error),
    )


def _safe_provider_name(provider_name: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in provider_name).strip("-")


class UnsafeRedirectError(ValueError):
    pass


class _BrowserRequestScopeGuard:
    def __init__(self, initial_scope: str):
        self.initial_scope = initial_scope
        self.installed = False
        self.installed_on: str | None = None
        self.blocked_requests: list[dict[str, object]] = []
        self.dns_cache: dict[tuple[str, int], dict[str, object]] = {}

    def route(self, route) -> None:
        request = getattr(route, "request", None)
        url = str(getattr(request, "url", "") or "")
        if not _browser_request_needs_scope_check(url):
            _continue_browser_route(route)
            return

        try:
            target_evidence = _target_scope_evidence_for_url(url, self.dns_cache)
            error = None
        except ValueError as exc:
            target_evidence = {"target_scope": "invalid", "resolved_addresses": [], "resolution_error": None}
            error = str(exc)

        if error or _target_scope_evidence_is_disallowed(self.initial_scope, target_evidence):
            self.blocked_requests.append(
                {
                    "url": url,
                    "method": getattr(request, "method", None),
                    "resource_type": getattr(request, "resource_type", None),
                    "target_scope": target_evidence.get("target_scope"),
                    "resolved_addresses": target_evidence.get("resolved_addresses", []),
                    "resolution_error": target_evidence.get("resolution_error"),
                    "error": error,
                }
            )
            _abort_browser_route(route)
            return

        _continue_browser_route(route)


class _TargetScopeRedirectHandler(HTTPRedirectHandler):
    def __init__(self, initial_scope: str):
        super().__init__()
        self.initial_scope = initial_scope
        self.redirects: list[dict[str, object]] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            target_evidence = _target_scope_evidence_for_url(newurl)
            error = None
        except ValueError as exc:
            target_evidence = {"target_scope": "invalid", "resolved_addresses": [], "resolution_error": None}
            error = str(exc)
        self.redirects.append(
            {
                "status_code": code,
                "from_url": req.full_url,
                "to_url": newurl,
                "target_scope": target_evidence.get("target_scope"),
                "resolved_addresses": target_evidence.get("resolved_addresses", []),
                "resolution_error": target_evidence.get("resolution_error"),
                "error": error,
            }
        )
        if error:
            raise UnsafeRedirectError(f"Raw HTTP redirect target is invalid or unsupported: {error}")
        if _target_scope_evidence_is_disallowed(self.initial_scope, target_evidence):
            target_scope = _first_disallowed_scope(self.initial_scope, target_evidence) or target_evidence.get("target_scope")
            raise UnsafeRedirectError(
                f"Raw HTTP redirect from {self.initial_scope} to {target_scope} target is blocked; create an approved run for the redirect target instead."
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _redirect_target_is_disallowed(initial_scope: str, target_scope: str) -> bool:
    return _target_scope_is_disallowed(initial_scope, target_scope)


def _target_scope_is_disallowed(initial_scope: str, target_scope: str) -> bool:
    return target_scope in SENSITIVE_TARGET_SCOPES and target_scope != initial_scope


def _target_scope_evidence_for_url(url: str, dns_cache: dict[tuple[str, int], dict[str, object]] | None = None) -> dict[str, object]:
    target_scope = target_scope_for_url(url)
    evidence: dict[str, object] = {
        "target_scope": target_scope,
        "resolved_addresses": [],
        "resolution_error": None,
    }
    if target_scope != "public_web":
        return evidence
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return evidence
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    cache_key = (parsed.hostname.strip().lower().rstrip("."), int(port))
    if dns_cache is not None and cache_key in dns_cache:
        resolution = dns_cache[cache_key]
    else:
        resolution = _resolve_host_target_scopes(cache_key[0], cache_key[1])
        if dns_cache is not None:
            dns_cache[cache_key] = resolution
    evidence.update(resolution)
    return evidence


def _resolve_host_target_scopes(host: str, port: int) -> dict[str, object]:
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return {"resolved_addresses": [], "resolution_error": str(exc)}

    seen: set[str] = set()
    resolved_addresses = []
    for record in records:
        sockaddr = record[4]
        if not sockaddr:
            continue
        address = str(sockaddr[0])
        if address in seen:
            continue
        seen.add(address)
        resolved_addresses.append({"address": address, "target_scope": _target_scope_for_address(address)})
    return {"resolved_addresses": resolved_addresses, "resolution_error": None}


def _target_scope_for_address(address: str) -> str:
    try:
        parsed = ip_address(address)
    except ValueError:
        return "private_network"
    if parsed.is_loopback:
        return "loopback"
    if parsed.is_link_local:
        return "link_local"
    if parsed.is_private or parsed.is_unspecified or parsed.is_reserved:
        return "private_network"
    return "public_web"


def _target_scope_evidence_is_disallowed(initial_scope: str, evidence: dict[str, object]) -> bool:
    if _target_scope_resolution_unverified(evidence):
        return True
    if _target_scope_is_disallowed(initial_scope, str(evidence.get("target_scope"))):
        return True
    for resolved in evidence.get("resolved_addresses", []):
        if isinstance(resolved, dict) and _target_scope_is_disallowed(initial_scope, str(resolved.get("target_scope"))):
            return True
    return False


def _first_disallowed_scope(initial_scope: str, evidence: dict[str, object]) -> str | None:
    if _target_scope_resolution_unverified(evidence):
        return "unresolved_public_web"
    target_scope = str(evidence.get("target_scope"))
    if _target_scope_is_disallowed(initial_scope, target_scope):
        return target_scope
    for resolved in evidence.get("resolved_addresses", []):
        if isinstance(resolved, dict):
            resolved_scope = str(resolved.get("target_scope"))
            if _target_scope_is_disallowed(initial_scope, resolved_scope):
                return resolved_scope
    return None


def _target_scope_resolution_unverified(evidence: dict[str, object]) -> bool:
    return (
        str(evidence.get("target_scope")) == "public_web"
        and not evidence.get("resolved_addresses")
        and bool(evidence.get("resolution_error"))
    )


def _browser_request_needs_scope_check(url: str) -> bool:
    if not url:
        return False
    return urlparse(url).scheme.lower() in BROWSER_SCOPED_REQUEST_SCHEMES


def _install_browser_request_scope_guard(page, context, task: TaskSpec) -> _BrowserRequestScopeGuard:
    guard = _BrowserRequestScopeGuard(task.target_scope)
    for target_name, target in (("context", context), ("page", page)):
        route = getattr(target, "route", None)
        if callable(route):
            route("**/*", guard.route)
            guard.installed = True
            guard.installed_on = target_name
            break
    return guard


def _continue_browser_route(route) -> None:
    continue_route = getattr(route, "continue_", None)
    if callable(continue_route):
        continue_route()


def _abort_browser_route(route) -> None:
    abort_route = getattr(route, "abort", None)
    if callable(abort_route):
        abort_route()


def _browser_scope_guard_check(guard: _BrowserRequestScopeGuard | None) -> str:
    if guard and guard.installed:
        return f"browser request target guard installed on {guard.installed_on}"
    return "browser request target guard unavailable in this browser binding"


def _browser_request_scope_block_result(
    provider_name: str,
    run_id: str,
    artifact_dir: Path,
    task: TaskSpec,
    guard: _BrowserRequestScopeGuard,
) -> ExecutionResult:
    meta_path = artifact_dir / "browser-request-scope-block.json"
    unresolved_count = len([request for request in guard.blocked_requests if request.get("target_scope") == "public_web" and request.get("resolution_error")])
    error = (
        "Browser request target DNS could not be verified; create a new run after DNS is resolvable or use an explicitly scoped target."
        if unresolved_count
        else "Browser request to a sensitive target scope was blocked; create an approved run for that target scope instead."
    )
    checks = [
        "browser request target was inspected",
        _browser_scope_guard_check(guard),
    ]
    checks.append("public target DNS resolution failed" if unresolved_count else "request to sensitive target scope was blocked")
    meta_path.write_text(
        _redacted_json_dump(
            {
                "run_id": run_id,
                "provider": provider_name,
                "url": task.url,
                "target_scope": task.target_scope,
                "guard_installed": guard.installed,
                "guard_installed_on": guard.installed_on,
                "blocked_requests": guard.blocked_requests,
            }
        ),
        encoding="utf-8",
    )
    return ExecutionResult(
        provider=provider_name,
        status="blocked",
        error=error,
        artifacts=[{"type": "metadata", "path": str(meta_path), "provider": provider_name, "blocked_requests": len(guard.blocked_requests)}],
        events=[_event("blocked", "browser_request_target_scope")],
        verification={
            "confidence": "high",
            "checks": checks,
        },
    )


def _raw_http_target_scope_block_result(
    provider_name: str,
    run_id: str,
    artifact_dir: Path,
    task: TaskSpec,
    target_evidence: dict[str, object],
) -> ExecutionResult:
    meta_path = artifact_dir / "raw-http-target-scope-block.json"
    blocked_scope = _first_disallowed_scope(task.target_scope, target_evidence) or target_evidence.get("target_scope")
    unresolved = blocked_scope == "unresolved_public_web"
    meta_path.write_text(
        _redacted_json_dump(
            {
                "run_id": run_id,
                "provider": provider_name,
                "url": task.url,
                "target_scope": task.target_scope,
                "blocked_scope": blocked_scope,
                "target_evidence": target_evidence,
            }
        ),
        encoding="utf-8",
    )
    return ExecutionResult(
        provider=provider_name,
        status="blocked",
        error=(
            "Raw HTTP target DNS could not be verified; retry after DNS is resolvable or use an explicitly scoped target."
            if unresolved
            else "Raw HTTP target resolved to a sensitive target scope; create an approved run for that target scope instead."
        ),
        artifacts=[{"type": "metadata", "path": str(meta_path), "provider": provider_name, "blocked_scope": blocked_scope}],
        events=[_event("blocked", "raw_http_resolved_target_scope")],
        verification={
            "confidence": "high",
            "checks": [
                "raw HTTP target DNS was inspected",
                "public target DNS resolution failed" if unresolved else "resolved target to sensitive scope was blocked",
            ],
        },
    )


def _provider_url_scope_preflight(provider_name: str, run_id: str, artifact_dir: Path, task: TaskSpec) -> ExecutionResult | None:
    if not task.url:
        return None
    target_evidence = _target_scope_evidence_for_url(task.url)
    if not _target_scope_evidence_is_disallowed(task.target_scope, target_evidence):
        return None
    return _provider_url_scope_block_result(provider_name, run_id, artifact_dir, task, target_evidence)


def _provider_url_scope_block_result(
    provider_name: str,
    run_id: str,
    artifact_dir: Path,
    task: TaskSpec,
    target_evidence: dict[str, object],
) -> ExecutionResult:
    meta_path = artifact_dir / "provider-url-scope-block.json"
    blocked_scope = _first_disallowed_scope(task.target_scope, target_evidence) or target_evidence.get("target_scope")
    unresolved = blocked_scope == "unresolved_public_web"
    meta_path.write_text(
        _redacted_json_dump(
            {
                "run_id": run_id,
                "provider": provider_name,
                "url": task.url,
                "target_scope": task.target_scope,
                "blocked_scope": blocked_scope,
                "target_evidence": target_evidence,
            }
        ),
        encoding="utf-8",
    )
    return ExecutionResult(
        provider=provider_name,
        status="blocked",
        error=(
            "Provider target URL DNS could not be verified locally; retry after DNS is resolvable or use an explicitly scoped target."
            if unresolved
            else "Provider target URL resolved to a sensitive target scope; create an approved run for that target scope instead."
        ),
        artifacts=[{"type": "metadata", "path": str(meta_path), "provider": provider_name, "blocked_scope": blocked_scope}],
        events=[_event("blocked", "provider_url_resolved_target_scope")],
        verification={
            "confidence": "high",
            "checks": [
                "provider target URL DNS was inspected",
                "public target DNS resolution failed" if unresolved else "resolved provider target to sensitive scope was blocked",
            ],
        },
    )


def _provider_transport_preflight(
    provider_name: str,
    run_id: str,
    artifact_dir: Path,
    env_name: str,
    url: str,
    allowed_schemes: set[str],
) -> ExecutionResult | None:
    if not os.environ.get(env_name):
        return None
    try:
        evidence = _provider_transport_evidence(url, allowed_schemes)
    except ValueError as exc:
        return _provider_transport_block_result(provider_name, run_id, artifact_dir, env_name, url, "invalid_provider_transport_url", str(exc), {})

    scheme = str(evidence.get("scheme", ""))
    target_evidence = evidence.get("target_evidence") if isinstance(evidence.get("target_evidence"), dict) else {}
    blocked_scope = _first_disallowed_scope("public_web", target_evidence) or target_evidence.get("target_scope")
    if blocked_scope in {"private_network", "link_local", "local_file"} and not _env_flag(ALLOW_INTERNAL_PROVIDER_BASES_ENV):
        return _provider_transport_block_result(
            provider_name,
            run_id,
            artifact_dir,
            env_name,
            url,
            "provider_transport_target_scope",
            f"{env_name} points at {blocked_scope}; set {ALLOW_INTERNAL_PROVIDER_BASES_ENV}=1 only for an intentional internal provider endpoint.",
            evidence,
        )
    if blocked_scope == "unresolved_public_web":
        return _provider_transport_block_result(
            provider_name,
            run_id,
            artifact_dir,
            env_name,
            url,
            "provider_transport_dns_unverified",
            f"{env_name} DNS could not be verified locally; fix the provider endpoint before sending credentials.",
            evidence,
        )
    if scheme in {"http", "ws"} and blocked_scope != "loopback" and not _env_flag(ALLOW_INSECURE_PROVIDER_BASES_ENV):
        return _provider_transport_block_result(
            provider_name,
            run_id,
            artifact_dir,
            env_name,
            url,
            "insecure_provider_transport_url",
            f"{env_name} uses insecure {scheme.upper()} transport outside loopback; use HTTPS/WSS or set {ALLOW_INSECURE_PROVIDER_BASES_ENV}=1 for an explicit override.",
            evidence,
        )
    return None


def _provider_transport_evidence(url: str, allowed_schemes: set[str]) -> dict[str, object]:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if not scheme:
        raise ValueError("provider transport URL must include a scheme")
    if scheme not in allowed_schemes:
        allowed = ", ".join(sorted(allowed_schemes))
        raise ValueError(f"provider transport URL must use one of these schemes: {allowed}")
    if parsed.username or parsed.password:
        raise ValueError("provider transport URL must not contain username or password credentials")
    if not parsed.hostname:
        raise ValueError("provider transport URL must include a host")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("provider transport URL port must be a valid integer") from exc
    scope_url = _transport_scope_url(url)
    target_evidence = _target_scope_evidence_for_url(scope_url)
    return {
        "scheme": scheme,
        "host": parsed.hostname,
        "port": parsed.port,
        "target_evidence": target_evidence,
        "allow_internal_provider_bases": _env_flag(ALLOW_INTERNAL_PROVIDER_BASES_ENV),
        "allow_insecure_provider_bases": _env_flag(ALLOW_INSECURE_PROVIDER_BASES_ENV),
    }


def _transport_scope_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme == "wss":
        replacement = "https"
    elif scheme == "ws":
        replacement = "http"
    else:
        replacement = scheme
    return parsed._replace(scheme=replacement).geturl()


def _provider_transport_block_result(
    provider_name: str,
    run_id: str,
    artifact_dir: Path,
    env_name: str,
    url: str,
    reason: str,
    message: str,
    evidence: dict[str, object],
) -> ExecutionResult:
    meta_path = artifact_dir / "provider-transport-block.json"
    meta_path.write_text(
        _redacted_json_dump(
            {
                "run_id": run_id,
                "provider": provider_name,
                "env_name": env_name,
                "url": url,
                "reason": reason,
                "message": message,
                "evidence": evidence,
            }
        ),
        encoding="utf-8",
    )
    return ExecutionResult(
        provider=provider_name,
        status="blocked",
        error=message,
        artifacts=[{"type": "metadata", "path": str(meta_path), "provider": provider_name, "env_name": env_name, "reason": reason}],
        events=[_event("blocked", reason)],
        verification={
            "confidence": "high",
            "checks": [
                "provider transport override was inspected before credentials were sent",
                message,
            ],
        },
    )


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _open_raw_http_request(request: Request, timeout_seconds: int, proxy: str | None, redirect_handler: _TargetScopeRedirectHandler):
    handlers = [redirect_handler]
    if proxy:
        handlers.append(ProxyHandler({"http": proxy, "https": proxy}))
    return build_opener(*handlers).open(request, timeout=timeout_seconds)


def _response_url(response, fallback_url: str) -> str:
    geturl = getattr(response, "geturl", None)
    if callable(geturl):
        return geturl()
    return fallback_url


class PlaywrightAdapter:
    name = "playwright"

    def execute(self, plan: Plan, run_id: str, artifact_dir: Path) -> ExecutionResult:
        task = plan.task
        if not task.url:
            return ExecutionResult(
                provider=self.name,
                status="blocked",
                error="Local Playwright execution requires a URL. Add --url or include a URL in the goal.",
                events=[_event("blocked", "missing_url")],
            )
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - depends on environment
            return ExecutionResult(
                provider=self.name,
                status="blocked",
                error=f"Playwright Python package is not installed: {exc}",
                events=[_event("blocked", "missing_playwright_package")],
            )

        screenshot_path = artifact_dir / "playwright-page.png"
        text_path = artifact_dir / "page-text.txt"
        meta_path = artifact_dir / "page-meta.json"
        navigation_timeout_ms = _timeout_milliseconds(task, 30_000)
        text_timeout_ms = _timeout_milliseconds(task, 10_000)
        scope_guard: _BrowserRequestScopeGuard | None = None
        close_error = None
        launch_kwargs: dict[str, Any] = {"headless": True}
        proxy_settings = playwright_proxy_settings(_task_proxy_url(task))
        if proxy_settings:
            launch_kwargs["proxy"] = proxy_settings
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(**launch_kwargs)
                try:
                    page = browser.new_page()
                    scope_guard = _install_browser_request_scope_guard(page, None, task)
                    page.goto(task.url, wait_until="domcontentloaded", timeout=navigation_timeout_ms)
                    if scope_guard.blocked_requests:
                        return _browser_request_scope_block_result(self.name, run_id, artifact_dir, task, scope_guard)
                    title = page.title()
                    text = page.locator("body").inner_text(timeout=text_timeout_ms)
                    if scope_guard.blocked_requests:
                        return _browser_request_scope_block_result(self.name, run_id, artifact_dir, task, scope_guard)
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    if scope_guard.blocked_requests:
                        return _browser_request_scope_block_result(self.name, run_id, artifact_dir, task, scope_guard)
                finally:
                    close_error = _safe_browser_close(browser)
        except PlaywrightError as exc:
            if scope_guard and scope_guard.blocked_requests:
                return _browser_request_scope_block_result(self.name, run_id, artifact_dir, task, scope_guard)
            return ExecutionResult(
                provider=self.name,
                status="blocked",
                error=f"Playwright could not run this task: {exc}",
                events=[_event("blocked", "playwright_runtime_unavailable")],
                verification={"confidence": "low", "checks": ["playwright package import succeeded", "browser execution failed"]},
            )
        except Exception as exc:
            return ExecutionResult(
                provider=self.name,
                status="failed",
                error=str(exc),
                events=[_event("failed", "playwright_execution_failed")],
            )

        text_path.write_text(redact_text(text) or "", encoding="utf-8")
        meta_path.write_text(
            _redacted_json_dump(
                {
                    "run_id": run_id,
                    "provider": self.name,
                    "url": task.url,
                    "target_scope": task.target_scope,
                    "title": title,
                    "text_length": len(text),
                    "screenshot": str(screenshot_path),
                    "profile": task.profile,
                    "used_proxy": bool(proxy_settings),
                }
            ),
            encoding="utf-8",
        )
        events = [_event("complete", "playwright_page_captured")]
        checks = ["navigated to URL", "captured body text", "captured screenshot", _browser_scope_guard_check(scope_guard)]
        if close_error:
            events.append(_event("warning", "browser_close_failed"))
            checks.append("browser close failed after capture")
        return ExecutionResult(
            provider=self.name,
            status="complete",
            artifacts=[
                {"type": "screenshot", "path": str(screenshot_path), "provider": self.name},
                {"type": "text", "path": str(text_path), "provider": self.name, "chars": len(text)},
                {"type": "metadata", "path": str(meta_path), "provider": self.name, "title": title},
            ],
            events=events,
            verification={"confidence": "high", "checks": checks},
        )


class RawHttpAdapter:
    name = "decodo-http"

    def execute(self, plan: Plan, run_id: str, artifact_dir: Path) -> ExecutionResult:
        task = plan.task
        if not task.url:
            return ExecutionResult(
                provider=self.name,
                status="blocked",
                error="Raw HTTP execution requires a URL. Add --url or include a URL in the goal.",
                events=[_event("blocked", "missing_url")],
            )
        if urlparse(task.url).scheme.lower() not in {"http", "https"}:
            return ExecutionResult(
                provider=self.name,
                status="blocked",
                error="Raw HTTP execution supports only http/https URLs; local file URLs must use Playwright.",
                events=[_event("blocked", "unsupported_raw_http_scheme")],
                verification={"confidence": "high", "checks": ["raw HTTP rejected unsupported URL scheme", "local file URLs are Playwright-only"]},
            )
        output_path = artifact_dir / "response-body.txt"
        meta_path = artifact_dir / "response-meta.json"
        target_evidence = _target_scope_evidence_for_url(task.url)
        if _target_scope_evidence_is_disallowed(task.target_scope, target_evidence):
            return _raw_http_target_scope_block_result(self.name, run_id, artifact_dir, task, target_evidence)
        proxy = _task_proxy_url(task) or os.environ.get("DECODO_PROXY")
        request = Request(task.url, headers={"User-Agent": "SuperBrowser/0.3 (+https://github.com/jbellsolutions/super-saiyan-browser)"})
        timeout_seconds = _timeout_seconds(task, 30)
        redirect_handler = _TargetScopeRedirectHandler(task.target_scope)
        try:
            response = _open_raw_http_request(request, timeout_seconds, proxy, redirect_handler)
            body = response.read()
            status = getattr(response, "status", response.getcode())
            headers = redact_headers(dict(response.headers.items()))
            final_url = _response_url(response, task.url)
            final_target_scope = target_scope_for_url(final_url)
        except UnsafeRedirectError as exc:
            meta_path.write_text(
                _redacted_json_dump(
                    {
                        "run_id": run_id,
                        "provider": self.name,
                        "url": task.url,
                        "target_scope": task.target_scope,
                        "redirects": redirect_handler.redirects,
                        "blocked_reason": str(exc),
                    }
                ),
                encoding="utf-8",
            )
            return ExecutionResult(
                provider=self.name,
                status="blocked",
                error=str(exc),
                artifacts=[{"type": "metadata", "path": str(meta_path), "provider": self.name, "redirects": len(redirect_handler.redirects)}],
                events=[_event("blocked", "raw_http_redirect_target_scope")],
                verification={
                    "confidence": "high",
                    "checks": [
                        "raw HTTP redirect target was inspected",
                        "redirect to sensitive target scope was blocked",
                    ],
                },
            )
        except TimeoutError as exc:
            return ExecutionResult(
                provider=self.name,
                status="failed",
                error=f"HTTP request timed out after {timeout_seconds} second(s): {exc}",
                events=[_event("failed", "raw_http_timeout")],
                verification={"confidence": "medium", "checks": [f"timeout_seconds={timeout_seconds}", "raw HTTP request timed out"]},
            )
        except URLError as exc:
            return ExecutionResult(
                provider=self.name,
                status="failed",
                error=f"HTTP request failed: {exc}",
                events=[_event("failed", "raw_http_request_failed")],
            )
        body_metadata = _write_redacted_http_body(output_path, body)
        meta_path.write_text(
            _redacted_json_dump(
                {
                    "run_id": run_id,
                    "provider": self.name,
                    "url": task.url,
                    "target_scope": task.target_scope,
                    "target_evidence": target_evidence,
                    "final_url": final_url,
                    "final_target_scope": final_target_scope,
                    "redirects": redirect_handler.redirects,
                    "status_code": status,
                    "bytes": len(body),
                    "saved_bytes": body_metadata["saved_bytes"],
                    "body_encoding": body_metadata["body_encoding"],
                    "body_redacted": body_metadata["body_redacted"],
                    "used_proxy": bool(proxy),
                    "timeout_seconds": timeout_seconds,
                    "headers": headers,
                }
            ),
            encoding="utf-8",
        )
        return ExecutionResult(
            provider=self.name,
            status="complete",
            artifacts=[
                {
                    "type": "http_response",
                    "path": str(output_path),
                    "provider": self.name,
                    "bytes": body_metadata["saved_bytes"],
                    "source_bytes": len(body),
                    "status_code": status,
                    "body_redacted": body_metadata["body_redacted"],
                },
                {"type": "metadata", "path": str(meta_path), "provider": self.name, "used_proxy": bool(proxy)},
            ],
            events=[_event("complete", "raw_http_response_captured")],
            verification={"confidence": "high", "checks": ["fetched URL", "saved response body", "saved response metadata", f"timeout_seconds={timeout_seconds}"]},
        )


class BrowserUseAdapter:
    name = "browser-use"

    def execute(self, plan: Plan, run_id: str, artifact_dir: Path) -> ExecutionResult:
        provider = PROVIDERS[self.name]
        missing = [env_name for env_name in provider.env_vars if not os.environ.get(env_name)]
        if missing:
            return _missing_credentials_result(self.name, missing)
        preflight = _provider_url_scope_preflight(self.name, run_id, artifact_dir, plan.task)
        if preflight:
            return preflight
        try:
            from browser_use_sdk.v3 import AsyncBrowserUse
        except Exception as exc:
            return ExecutionResult(
                provider=self.name,
                status="blocked",
                error=f"Browser Use SDK is not installed or importable: {exc}. Install with `pip install browser-use-sdk`.",
                artifacts=[{"type": "provider_docs", "provider": self.name, "url": provider.docs_url}],
                events=[_event("blocked", "missing_browser_use_sdk")],
                verification={"confidence": "low", "checks": ["credentials present", "sdk import failed"]},
            )

        timeout_seconds = _timeout_seconds(plan.task, 600)
        try:
            result_payload = asyncio.run(self._run_browser_use(AsyncBrowserUse, plan.task))
        except TimeoutError as exc:
            return ExecutionResult(
                provider=self.name,
                status="failed",
                error=f"Browser Use execution timed out after {timeout_seconds} second(s): {exc}",
                events=[_event("failed", "browser_use_timeout")],
                verification={"confidence": "medium", "checks": [f"timeout_seconds={timeout_seconds}", "Browser Use execution timed out"]},
            )
        except Exception as exc:
            return ExecutionResult(
                provider=self.name,
                status="failed",
                error=f"Browser Use execution failed: {exc}",
                events=[_event("failed", "browser_use_execution_failed")],
            )

        output_path = artifact_dir / "browser-use-output.json"
        output_path.write_text(_redacted_json_dump(result_payload), encoding="utf-8")
        failure_reason = _provider_payload_failure_reason(result_payload, self.name)
        artifacts = [{"type": "provider_output", "path": str(output_path), "provider": self.name}]
        for key in ("liveUrl", "live_url", "recordingUrl", "recording_url", "screenshotUrl", "screenshot_url"):
            if result_payload.get(key):
                artifacts.append({"type": key, "provider": self.name, "url": result_payload[key]})
        return ExecutionResult(
            provider=self.name,
            status="failed" if failure_reason else "complete",
            artifacts=artifacts,
            events=[_event("failed" if failure_reason else "complete", "browser_use_task_failed" if failure_reason else "browser_use_task_complete")],
            verification={
                "confidence": "low" if failure_reason else "medium",
                "checks": ["Browser Use SDK run returned", "output saved", "provider payload checked for explicit failure", f"timeout_seconds={timeout_seconds}"],
            },
            error=failure_reason,
        )

    async def _run_browser_use(self, client_class, task: TaskSpec) -> dict:
        client = client_class()
        prompt = _task_prompt(task)
        run_kwargs: dict[str, Any] = {}
        profile_id = _browser_use_profile_id(task)
        if profile_id:
            run_kwargs["profile_id"] = profile_id
        result = await asyncio.wait_for(client.run(prompt, **run_kwargs), timeout=_timeout_seconds(task, 600))
        payload = _object_to_payload(result)
        if profile_id and task.profile:
            payload.setdefault("profile", task.profile)
            payload.setdefault("profile_id", profile_id)
        return payload


class OrgoAdapter:
    name = "orgo"

    def execute(self, plan: Plan, run_id: str, artifact_dir: Path) -> ExecutionResult:
        provider = PROVIDERS[self.name]
        missing = [env_name for env_name in provider.env_vars if not os.environ.get(env_name)]
        if missing:
            return _missing_credentials_result(self.name, missing)
        preflight = _provider_url_scope_preflight(self.name, run_id, artifact_dir, plan.task)
        if preflight:
            return preflight
        api_base = os.environ.get("ORGO_API_BASE", "https://www.orgo.ai/api").rstrip("/")
        transport_preflight = _provider_transport_preflight(self.name, run_id, artifact_dir, "ORGO_API_BASE", api_base, PROVIDER_TRANSPORT_HTTP_SCHEMES)
        if transport_preflight:
            return transport_preflight
        timeout_seconds = _timeout_seconds(plan.task, 600)
        auth_headers = {"Authorization": f"Bearer {os.environ['ORGO_API_KEY']}"}
        try:
            computer_id, computer_source = _orgo_resolve_computer_id(api_base, auth_headers, timeout_seconds)
        except Exception as exc:
            return ExecutionResult(
                provider=self.name,
                status="failed",
                error=f"Orgo computer discovery failed: {exc}",
                artifacts=[{"type": "provider_docs", "provider": self.name, "url": provider.docs_url}],
                events=[_event("failed", "orgo_computer_discovery_failed")],
                verification={"confidence": "low", "checks": ["attempted Orgo workspace/computer discovery", "no ORGO_COMPUTER_ID pinned", f"timeout_seconds={timeout_seconds}"]},
            )
        try:
            chat_payload = _http_json(
                _orgo_chat_completions_url(api_base),
                {
                    "model": os.environ.get("ORGO_MODEL", "claude-sonnet-4-6"),
                    "computer_id": computer_id,
                    "messages": [{"role": "user", "content": _task_prompt(plan.task)}],
                    "stream": False,
                },
                auth_headers,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            return ExecutionResult(
                provider=self.name,
                status="failed",
                error=f"Orgo computer-use request failed: {exc}",
                artifacts=[{"type": "provider_docs", "provider": self.name, "url": provider.docs_url}],
                events=[_event("failed", "orgo_chat_failed")],
                verification={"confidence": "low", "checks": ["submitted Orgo computer-use agent task", "provider request failed", f"timeout_seconds={timeout_seconds}"]},
            )
        output_path = artifact_dir / "orgo-agent-output.json"
        screenshot_path = artifact_dir / "orgo-screenshot.json"
        output_path.write_text(_redacted_json_dump(chat_payload), encoding="utf-8")
        try:
            screenshot_payload = _http_json(
                f"{api_base}/computers/{computer_id}/screenshot",
                None,
                auth_headers,
                method="GET",
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            failure_reason = _provider_payload_failure_reason(chat_payload, self.name)
            error = f"Orgo screenshot request failed: {exc}"
            if failure_reason:
                error = f"{failure_reason}; {error}"
            return ExecutionResult(
                provider=self.name,
                status="failed",
                artifacts=[{"type": "provider_output", "path": str(output_path), "provider": self.name, "computer_id": computer_id, "success": False}],
                events=[_event("failed", "orgo_screenshot_failed")],
                verification={
                    "confidence": "low",
                    "checks": [
                        "submitted Orgo computer-use agent task",
                        "screenshot request failed",
                        "provider payload checked for explicit failure",
                        f"timeout_seconds={timeout_seconds}",
                    ],
                },
                error=error,
            )
        screenshot_path.write_text(_redacted_json_dump(screenshot_payload), encoding="utf-8")
        failure_reason = _provider_payload_failure_reason(chat_payload, self.name)
        success = failure_reason is None
        return ExecutionResult(
            provider=self.name,
            status="complete" if success else "failed",
            artifacts=[
                {"type": "provider_output", "path": str(output_path), "provider": self.name, "computer_id": computer_id, "success": success},
                {"type": "screenshot_json", "path": str(screenshot_path), "provider": self.name, "computer_id": computer_id},
            ],
            events=[_event("complete" if success else "failed", "orgo_agent_and_screenshot_captured")],
            verification={
                "confidence": "medium" if success else "low",
                "checks": [f"computer: {computer_source}", "submitted Orgo computer-use agent task", "requested screenshot", "provider payload checked for explicit failure", f"timeout_seconds={timeout_seconds}"],
            },
            error=failure_reason,
        )


AIRTOP_SESSION_READY_TIMEOUT_SECONDS = 120
AIRTOP_SESSION_POLL_INTERVAL_SECONDS = 2.0


def _airtop_wait_for_session_running(api_base: str, session_id: str, headers: dict[str, str], timeout_seconds: int) -> str:
    """Airtop sessions start as `initializing`; window APIs 404 until the session is `running`."""
    deadline = time.monotonic() + min(timeout_seconds, AIRTOP_SESSION_READY_TIMEOUT_SECONDS)
    status = "unknown"
    while time.monotonic() < deadline:
        payload = _http_json(f"{api_base}/sessions/{session_id}", None, headers, method="GET", timeout_seconds=timeout_seconds)
        status = str(_payload_get(payload, "data.status") or "unknown")
        if status == "running":
            return status
        if status in {"terminated", "error", "failed"}:
            raise RuntimeError(f"Airtop session {session_id} entered terminal status {status!r} before becoming ready")
        time.sleep(AIRTOP_SESSION_POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"Airtop session {session_id} did not reach running status in time (last status {status!r})")


class AirtopAdapter:
    name = "airtop"

    def execute(self, plan: Plan, run_id: str, artifact_dir: Path) -> ExecutionResult:
        provider = PROVIDERS[self.name]
        missing = [env_name for env_name in provider.env_vars if not os.environ.get(env_name)]
        if missing:
            return _missing_credentials_result(self.name, missing)
        if not plan.task.url:
            return ExecutionResult(
                provider=self.name,
                status="blocked",
                error="Airtop execution requires a URL. Add --url or include a URL in the goal.",
                events=[_event("blocked", "missing_url")],
            )
        preflight = _provider_url_scope_preflight(self.name, run_id, artifact_dir, plan.task)
        if preflight:
            return preflight

        api_base = os.environ.get("AIRTOP_API_BASE", "https://api.airtop.ai/api/v1").rstrip("/")
        transport_preflight = _provider_transport_preflight(self.name, run_id, artifact_dir, "AIRTOP_API_BASE", api_base, PROVIDER_TRANSPORT_HTTP_SCHEMES)
        if transport_preflight:
            return transport_preflight
        headers = {"Authorization": f"Bearer {os.environ['AIRTOP_API_KEY']}"}
        session_id = None
        timeout_seconds = _timeout_seconds(plan.task, 600)
        try:
            session_payload = _http_json(
                f"{api_base}/sessions",
                {"configuration": _airtop_session_configuration(plan.task)},
                headers,
                timeout_seconds=timeout_seconds,
            )
            session_id = _payload_get(session_payload, "data.id")
            if not session_id:
                raise RuntimeError("Airtop session response did not include data.id")
            _airtop_wait_for_session_running(api_base, session_id, headers, timeout_seconds)
            window_payload = _http_json(
                f"{api_base}/sessions/{session_id}/windows",
                {"url": plan.task.url, "waitUntil": "domContentLoaded"},
                headers,
                timeout_seconds=timeout_seconds,
            )
            window_id = _payload_get(window_payload, "data.windowId")
            if not window_id:
                raise RuntimeError("Airtop window response did not include data.windowId")
            query_payload = _http_json(
                f"{api_base}/sessions/{session_id}/windows/{window_id}/page-query",
                {"prompt": _task_prompt(plan.task)},
                headers,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            return ExecutionResult(
                provider=self.name,
                status="failed",
                error=f"Airtop execution failed: {exc}",
                artifacts=[{"type": "provider_docs", "provider": self.name, "url": provider.docs_url}],
                events=[_event("failed", "airtop_execution_failed")],
            )
        finally:
            if session_id:
                try:
                    _http_json(f"{api_base}/sessions/{session_id}", None, headers, method="DELETE", timeout_seconds=timeout_seconds)
                except Exception:
                    pass

        output_path = artifact_dir / "airtop-output.json"
        output_path.write_text(
            _redacted_json_dump(
                {
                    "run_id": run_id,
                    "provider": self.name,
                    "session": session_payload,
                    "window": window_payload,
                    "query": query_payload,
                    "timeout_seconds": timeout_seconds,
                }
            ),
            encoding="utf-8",
        )
        failure_reason = _provider_payload_failure_reason(query_payload, self.name)
        return ExecutionResult(
            provider=self.name,
            status="failed" if failure_reason else "complete",
            artifacts=[
                {
                    "type": "provider_output",
                    "path": str(output_path),
                    "provider": self.name,
                    "session_id": session_id,
                    "window_id": window_id,
                }
            ],
            events=[_event("failed" if failure_reason else "complete", "airtop_page_query_failed" if failure_reason else "airtop_page_query_complete")],
            verification={
                "confidence": "low" if failure_reason else "medium",
                "checks": ["created Airtop session", "opened Airtop window", "queried Airtop page", "provider payload checked for explicit failure", f"timeout_seconds={timeout_seconds}"],
            },
            error=failure_reason,
        )


class HyperbrowserAdapter:
    name = "hyperbrowser"

    def execute(self, plan: Plan, run_id: str, artifact_dir: Path) -> ExecutionResult:
        provider = PROVIDERS[self.name]
        missing = [env_name for env_name in provider.env_vars if not os.environ.get(env_name)]
        if missing:
            return _missing_credentials_result(self.name, missing)
        if not plan.task.url:
            return ExecutionResult(
                provider=self.name,
                status="blocked",
                error="Hyperbrowser execution requires a URL. Add --url or include a URL in the goal.",
                events=[_event("blocked", "missing_url")],
            )
        preflight = _provider_url_scope_preflight(self.name, run_id, artifact_dir, plan.task)
        if preflight:
            return preflight

        api_base = os.environ.get("HYPERBROWSER_API_BASE", "https://api.hyperbrowser.ai/api").rstrip("/")
        transport_preflight = _provider_transport_preflight(self.name, run_id, artifact_dir, "HYPERBROWSER_API_BASE", api_base, PROVIDER_TRANSPORT_HTTP_SCHEMES)
        if transport_preflight:
            return transport_preflight
        headers = {"x-api-key": os.environ["HYPERBROWSER_API_KEY"]}
        timeout_seconds = _timeout_seconds(plan.task, 600)
        scrape_body = {
            "url": plan.task.url,
            "sessionOptions": _hyperbrowser_session_options(plan.task),
            "scrapeOptions": {
                "formats": ["markdown", "html", "links"],
                "onlyMainContent": False,
                "timeout": int(os.environ.get("HYPERBROWSER_TIMEOUT_MS", "30000")),
            },
        }
        try:
            payload = _http_json(f"{api_base}/scrape", scrape_body, headers, timeout_seconds=timeout_seconds)
            job_id = payload.get("jobId") or payload.get("id")
            if job_id:
                payload = self._poll_scrape(api_base, headers, job_id, timeout_seconds)
            else:
                payload = dict(payload)
                payload.setdefault("error", "Hyperbrowser scrape response did not include jobId")
        except Exception as exc:
            return ExecutionResult(
                provider=self.name,
                status="failed",
                error=f"Hyperbrowser scrape failed: {exc}",
                artifacts=[{"type": "provider_docs", "provider": self.name, "url": provider.docs_url}],
                events=[_event("failed", "hyperbrowser_scrape_failed")],
            )

        output_path = artifact_dir / "hyperbrowser-output.json"
        output_path.write_text(_redacted_json_dump({"run_id": run_id, "provider": self.name, "result": payload, "timeout_seconds": timeout_seconds}), encoding="utf-8")
        failure_reason = _provider_payload_failure_reason(payload, self.name)
        return ExecutionResult(
            provider=self.name,
            status="failed" if failure_reason else "complete",
            artifacts=[{"type": "provider_output", "path": str(output_path), "provider": self.name, "job_id": payload.get("jobId") or payload.get("id")}],
            events=[_event("failed" if failure_reason else "complete", "hyperbrowser_scrape_failed" if failure_reason else "hyperbrowser_scrape_complete")],
            verification={
                "confidence": "low" if failure_reason else "medium",
                "checks": ["submitted Hyperbrowser scrape", "saved scrape result", "provider payload checked for explicit failure", f"timeout_seconds={timeout_seconds}"],
            },
            error=failure_reason,
        )

    def _poll_scrape(self, api_base: str, headers: dict[str, str], job_id: str, timeout_seconds: int) -> dict:
        attempts = int(os.environ.get("HYPERBROWSER_POLL_ATTEMPTS", "10"))
        delay = float(os.environ.get("HYPERBROWSER_POLL_SECONDS", "2"))
        latest = {}
        for _ in range(attempts):
            if delay:
                time.sleep(delay)
            latest = _http_json(f"{api_base}/scrape/{job_id}/status", None, headers, method="GET", timeout_seconds=timeout_seconds)
            status = _payload_status_value(latest)
            if status in SUCCESS_PROVIDER_STATUSES:
                return self._scrape_result_payload(api_base, headers, job_id, latest, status, timeout_seconds)
            if status in FAILED_PROVIDER_STATUSES:
                return self._scrape_result_payload(api_base, headers, job_id, latest, status, timeout_seconds, authoritative_status=True)
            if status and status not in UNFINISHED_PROVIDER_STATUSES:
                return {"jobId": job_id, "status": status, "status_response": latest, "error": f"unexpected status={status}"}
        status = _payload_status_value(latest) or "unknown"
        return {"jobId": job_id, "status": status, "status_response": latest, "error": f"unfinished status={status}"}

    def _scrape_result_payload(
        self,
        api_base: str,
        headers: dict[str, str],
        job_id: str,
        status_payload: dict,
        fallback_status: str,
        timeout_seconds: int,
        authoritative_status: bool = False,
    ) -> dict:
        result_payload = _http_json(f"{api_base}/scrape/{job_id}", None, headers, method="GET", timeout_seconds=timeout_seconds)
        if isinstance(result_payload, dict):
            result = dict(result_payload)
            result.setdefault("jobId", job_id)
            if authoritative_status:
                result["status"] = fallback_status
            else:
                result.setdefault("status", fallback_status)
            result["status_response"] = status_payload
            return result
        return {"jobId": job_id, "status": fallback_status, "status_response": status_payload, "data": result_payload}


class SteelAdapter:
    name = "steel"

    def execute(self, plan: Plan, run_id: str, artifact_dir: Path) -> ExecutionResult:
        provider = PROVIDERS[self.name]
        missing = [env_name for env_name in provider.env_vars if not os.environ.get(env_name)]
        if missing:
            return _missing_credentials_result(self.name, missing)
        if not plan.task.url:
            return ExecutionResult(
                provider=self.name,
                status="blocked",
                error="Steel execution requires a URL. Add --url or include a URL in the goal.",
                events=[_event("blocked", "missing_url")],
            )
        preflight = _provider_url_scope_preflight(self.name, run_id, artifact_dir, plan.task)
        if preflight:
            return preflight
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            return ExecutionResult(
                provider=self.name,
                status="blocked",
                error=f"Steel execution requires Playwright Python package: {exc}",
                artifacts=[{"type": "provider_docs", "provider": self.name, "url": provider.docs_url}],
                events=[_event("blocked", "missing_playwright_package")],
                verification={"confidence": "low", "checks": ["credentials present", "Playwright import failed"]},
            )

        api_key = os.environ["STEEL_API_KEY"]
        api_base = (os.environ.get("STEEL_API_BASE") or "https://api.steel.dev/v1").rstrip("/")
        rest_headers = {"steel-api-key": api_key}
        session_id = ""
        cdp_url = os.environ.get("STEEL_CDP_URL") or ""
        if cdp_url:
            transport_preflight = _provider_transport_preflight(self.name, run_id, artifact_dir, "STEEL_CDP_URL", cdp_url, PROVIDER_TRANSPORT_CDP_SCHEMES)
            if transport_preflight:
                return transport_preflight
        else:
            try:
                session_payload = _http_json(
                    f"{api_base}/sessions",
                    _steel_session_body(plan.task),
                    rest_headers,
                    timeout_seconds=_timeout_seconds(plan.task, 60),
                )
            except Exception as exc:
                return ExecutionResult(
                    provider=self.name,
                    status="blocked",
                    error=f"Steel session creation failed: {exc}",
                    events=[_event("blocked", "steel_session_create_failed")],
                )
            session_id = str(_payload_get(session_payload, "id") or _payload_get(session_payload, "data.id") or "")
            if not session_id:
                return ExecutionResult(
                    provider=self.name,
                    status="blocked",
                    error="Steel session response did not include a session id",
                    events=[_event("blocked", "steel_session_missing_id")],
                )
            cdp_url = f"wss://connect.steel.dev?{urlencode({'apiKey': api_key, 'sessionId': session_id})}"
        screenshot_path = artifact_dir / "steel-page.png"
        text_path = artifact_dir / "steel-page-text.txt"
        meta_path = artifact_dir / "steel-meta.json"
        navigation_timeout_ms = _timeout_milliseconds(plan.task, 30_000)
        text_timeout_ms = _timeout_milliseconds(plan.task, 10_000)
        scope_guard: _BrowserRequestScopeGuard | None = None
        close_error = None
        try:
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.connect_over_cdp(cdp_url)
                    try:
                        context = browser.contexts[0] if browser.contexts else browser.new_context()
                        page = context.pages[0] if context.pages else context.new_page()
                        scope_guard = _install_browser_request_scope_guard(page, context, plan.task)
                        page.goto(plan.task.url, wait_until="domcontentloaded", timeout=navigation_timeout_ms)
                        if scope_guard.blocked_requests:
                            return _browser_request_scope_block_result(self.name, run_id, artifact_dir, plan.task, scope_guard)
                        title = page.title()
                        text = page.locator("body").inner_text(timeout=text_timeout_ms)
                        if scope_guard.blocked_requests:
                            return _browser_request_scope_block_result(self.name, run_id, artifact_dir, plan.task, scope_guard)
                        page.screenshot(path=str(screenshot_path), full_page=True)
                        if scope_guard.blocked_requests:
                            return _browser_request_scope_block_result(self.name, run_id, artifact_dir, plan.task, scope_guard)
                    finally:
                        close_error = _safe_browser_close(browser)
            except PlaywrightError as exc:
                if scope_guard and scope_guard.blocked_requests:
                    return _browser_request_scope_block_result(self.name, run_id, artifact_dir, plan.task, scope_guard)
                return ExecutionResult(
                    provider=self.name,
                    status="blocked",
                    error=f"Steel Playwright connection failed: {exc}",
                    events=[_event("blocked", "steel_playwright_failed")],
                )
            except Exception as exc:
                return ExecutionResult(
                    provider=self.name,
                    status="failed",
                    error=f"Steel execution failed: {exc}",
                    events=[_event("failed", "steel_execution_failed")],
                )
        finally:
            if session_id:
                try:
                    _http_json(f"{api_base}/sessions/{session_id}/release", {}, rest_headers, timeout_seconds=30)
                except Exception:
                    pass

        text_path.write_text(redact_text(text) or "", encoding="utf-8")
        meta_path.write_text(
            _redacted_json_dump(
                {
                    "run_id": run_id,
                    "provider": self.name,
                    "session_id": session_id or None,
                    "url": plan.task.url,
                    "title": title,
                    "text_length": len(text),
                    "cdp_url_host": "connect.steel.dev",
                    "timeout_seconds": plan.task.timeout_seconds,
                    "profile": plan.task.profile,
                    "used_proxy": bool(_task_proxy_url(plan.task)),
                }
            ),
            encoding="utf-8",
        )
        events = [_event("complete", "steel_page_captured")]
        checks = [
            "connected to Steel over CDP",
            "captured page artifacts",
            f"timeout_seconds={_timeout_seconds(plan.task, 30)}",
            _browser_scope_guard_check(scope_guard),
        ]
        if close_error:
            events.append(_event("warning", "browser_close_failed"))
            checks.append("browser close failed after capture")
        return ExecutionResult(
            provider=self.name,
            status="complete",
            artifacts=[
                {"type": "screenshot", "path": str(screenshot_path), "provider": self.name},
                {"type": "text", "path": str(text_path), "provider": self.name, "chars": len(text)},
                {"type": "metadata", "path": str(meta_path), "provider": self.name},
            ],
            events=events,
            verification={
                "confidence": "medium",
                "checks": checks,
            },
        )


class BrightDataUnlockerAdapter:
    name = "brightdata-unlocker"

    def execute(self, plan: Plan, run_id: str, artifact_dir: Path) -> ExecutionResult:
        provider = PROVIDERS[self.name]
        missing = missing_env_for_lane(self.name)
        if missing:
            return _missing_credentials_result(self.name, missing)
        if not plan.task.url:
            return ExecutionResult(
                provider=self.name,
                status="blocked",
                error="Bright Data Unlocker requires a URL. Add --url or include a URL in the goal.",
                events=[_event("blocked", "missing_url")],
            )
        preflight = _provider_url_scope_preflight(self.name, run_id, artifact_dir, plan.task)
        if preflight:
            return preflight
        try:
            payload = unlock_url(plan.task.url, timeout_seconds=_timeout_seconds(plan.task, 120))
        except BrightDataError as exc:
            return ExecutionResult(
                provider=self.name,
                status="failed" if exc.error_class == "retryable" else "blocked" if exc.error_class == "auth" else "failed",
                error=f"Bright Data Unlocker failed: {exc}",
                events=[_event("failed", "brightdata_unlocker_failed")],
            )
        output_path = artifact_dir / "brightdata-unlocker-output.json"
        markdown_path = artifact_dir / "brightdata-unlocker.md"
        markdown_path.write_text(redact_text(str(payload.get("content") or "")), encoding="utf-8")
        output_path.write_text(_redacted_json_dump({"run_id": run_id, "provider": self.name, "result": payload}), encoding="utf-8")
        content_len = int(payload.get("content_length") or 0)
        return ExecutionResult(
            provider=self.name,
            status="complete" if content_len > 0 else "failed",
            artifacts=[
                {"type": "provider_output", "path": str(output_path), "provider": self.name},
                {"type": "markdown", "path": str(markdown_path), "provider": self.name, "chars": content_len},
            ],
            events=[_event("complete" if content_len > 0 else "failed", "brightdata_unlocker_complete")],
            verification={
                "confidence": "medium" if content_len > 0 else "low",
                "checks": ["submitted Bright Data unlock request", "saved markdown artifact", f"content_length={content_len}"],
            },
            error=None if content_len > 0 else "Bright Data Unlocker returned empty content",
        )


class BrightDataSerpAdapter:
    name = "brightdata-serp"

    def execute(self, plan: Plan, run_id: str, artifact_dir: Path) -> ExecutionResult:
        missing = missing_env_for_lane(self.name)
        if missing:
            return _missing_credentials_result(self.name, missing)
        query = plan.task.serp_query or _serp_query_from_goal(plan.task.goal)
        if not query:
            return ExecutionResult(
                provider=self.name,
                status="blocked",
                error="Bright Data SERP requires a search query in the goal or --query.",
                events=[_event("blocked", "missing_serp_query")],
            )
        try:
            payload = brightdata_search(
                query,
                engine=plan.task.serp_engine,
                geo=plan.task.serp_geo,
                timeout_seconds=_timeout_seconds(plan.task, 120),
            )
        except BrightDataError as exc:
            return ExecutionResult(
                provider=self.name,
                status="failed",
                error=f"Bright Data SERP failed: {exc}",
                events=[_event("failed", "brightdata_serp_failed")],
            )
        output_path = artifact_dir / "brightdata-serp-output.json"
        output_path.write_text(_redacted_json_dump({"run_id": run_id, "provider": self.name, "result": payload}), encoding="utf-8")
        return ExecutionResult(
            provider=self.name,
            status="complete",
            artifacts=[{"type": "provider_output", "path": str(output_path), "provider": self.name, "query": query}],
            events=[_event("complete", "brightdata_serp_complete")],
            verification={"confidence": "medium", "checks": ["submitted Bright Data SERP request", "saved SERP artifact", f"engine={plan.task.serp_engine}"]},
        )


class BrightDataDatasetAdapter:
    name = "brightdata-dataset"

    def execute(self, plan: Plan, run_id: str, artifact_dir: Path) -> ExecutionResult:
        missing = missing_env_for_lane(self.name)
        if missing:
            return _missing_credentials_result(self.name, missing)
        try:
            if plan.task.dataset_filter:
                dataset_id = str(plan.task.dataset_filter.get("dataset_id") or "gd_l1viktl72bvl7bjuj0")
                filter_tree = plan.task.dataset_filter.get("filter") or plan.task.dataset_filter
                if "dataset_id" in filter_tree:
                    filter_tree = {key: value for key, value in filter_tree.items() if key not in {"dataset_id", "size"}}
                payload = search_dataset(
                    dataset_id,
                    filter_tree,
                    size=int(plan.task.dataset_filter.get("size") or 10),
                    timeout_seconds=_timeout_seconds(plan.task, 180),
                )
            elif plan.task.url:
                preflight = _provider_url_scope_preflight(self.name, run_id, artifact_dir, plan.task)
                if preflight:
                    return preflight
                payload = scrape_dataset_url(
                    plan.task.url,
                    tool=plan.task.dataset_tool,
                    timeout_seconds=_timeout_seconds(plan.task, 120),
                )
            else:
                return ExecutionResult(
                    provider=self.name,
                    status="blocked",
                    error="Bright Data Dataset requires a platform URL or dataset filter.",
                    events=[_event("blocked", "missing_dataset_input")],
                )
        except BrightDataError as exc:
            return ExecutionResult(
                provider=self.name,
                status="failed",
                error=f"Bright Data Dataset failed: {exc}",
                events=[_event("failed", "brightdata_dataset_failed")],
            )
        output_path = artifact_dir / "brightdata-dataset-output.json"
        output_path.write_text(_redacted_json_dump({"run_id": run_id, "provider": self.name, "result": payload}), encoding="utf-8")
        return ExecutionResult(
            provider=self.name,
            status="complete",
            artifacts=[{"type": "provider_output", "path": str(output_path), "provider": self.name}],
            events=[_event("complete", "brightdata_dataset_complete")],
            verification={"confidence": "medium", "checks": ["submitted Bright Data dataset request", "saved structured dataset artifact"]},
        )


class BrightDataBrowserAdapter:
    name = "brightdata-browser"

    def execute(self, plan: Plan, run_id: str, artifact_dir: Path) -> ExecutionResult:
        missing = missing_env_for_lane(self.name)
        if missing:
            return _missing_credentials_result(self.name, missing)
        if not plan.task.url:
            return ExecutionResult(
                provider=self.name,
                status="blocked",
                error="Bright Data Browser requires a URL. Add --url or include a URL in the goal.",
                events=[_event("blocked", "missing_url")],
            )
        preflight = _provider_url_scope_preflight(self.name, run_id, artifact_dir, plan.task)
        if preflight:
            return preflight
        try:
            payload = scrape_with_browser(plan.task.url, timeout_seconds=_timeout_seconds(plan.task, 120))
        except BrightDataBrowserError as exc:
            return ExecutionResult(
                provider=self.name,
                status="failed" if "requires Playwright" not in str(exc) else "blocked",
                error=f"Bright Data Browser failed: {exc}",
                events=[_event("failed", "brightdata_browser_failed")],
            )
        screenshot_path = artifact_dir / "brightdata-browser.png"
        text_path = artifact_dir / "brightdata-browser-text.txt"
        html_path = artifact_dir / "brightdata-browser.html"
        meta_path = artifact_dir / "brightdata-browser-meta.json"
        screenshot_path.write_bytes(payload.pop("screenshot_bytes", b"") or b"")
        text_path.write_text(redact_text(str(payload.get("text") or "")), encoding="utf-8")
        html_path.write_text(redact_text(str(payload.get("html") or "")), encoding="utf-8")
        meta_path.write_text(_redacted_json_dump({"run_id": run_id, "provider": self.name, "result": payload}), encoding="utf-8")
        return ExecutionResult(
            provider=self.name,
            status="complete",
            artifacts=[
                {"type": "screenshot", "path": str(screenshot_path), "provider": self.name},
                {"type": "text", "path": str(text_path), "provider": self.name, "chars": payload.get("text_length", 0)},
                {"type": "html", "path": str(html_path), "provider": self.name},
                {"type": "metadata", "path": str(meta_path), "provider": self.name},
            ],
            events=[_event("complete", "brightdata_browser_complete")],
            verification={"confidence": "medium", "checks": ["connected Bright Data browser over CDP", "saved page artifacts"]},
        )


class ExternalProviderAdapter:
    def __init__(self, provider_name: str):
        self.name = provider_name

    def execute(self, plan: Plan, run_id: str, artifact_dir: Path) -> ExecutionResult:
        provider = PROVIDERS[self.name]
        missing = [env_name for env_name in provider.env_vars if not os.environ.get(env_name)]
        if missing:
            return _missing_credentials_result(self.name, missing)
        return ExecutionResult(
            provider=self.name,
            status="blocked",
            error=f"{provider.display_name} adapter is documented but live execution is not implemented in this local runtime yet.",
            artifacts=[{"type": "provider_docs", "provider": self.name, "url": provider.docs_url}],
            events=[_event("blocked", "adapter_not_implemented")],
            verification={"confidence": "low", "checks": ["provider selected", "adapter pending implementation"]},
        )


def _serp_query_from_goal(goal: str) -> str | None:
    text = goal.strip()
    lowered = text.lower()
    prefixes = (
        "google search:",
        "bing search:",
        "serp:",
        "search results for",
        "search for",
        "google:",
    )
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return text[len(prefix) :].strip()
    if lowered.startswith("search "):
        return text[7:].strip()
    return None


def _missing_credentials_result(provider_name: str, missing: list[str]) -> ExecutionResult:
    provider = PROVIDERS[provider_name]
    return ExecutionResult(
        provider=provider_name,
        status="blocked",
        error=f"{provider.display_name} requires missing env vars: {', '.join(missing)}",
        artifacts=[{"type": "provider_docs", "provider": provider_name, "url": provider.docs_url, "missing_env": missing}],
        events=[_event("blocked", "missing_provider_credentials")],
        verification={"confidence": "low", "checks": ["provider selected", "credentials missing"]},
    )


def _http_json(url: str, body: dict | None, headers: dict[str, str], method: str = "POST", timeout_seconds: int | None = None) -> dict:
    # Some provider edges (e.g. Steel behind Cloudflare) reject the default Python-urllib user agent with 403.
    request_headers = {"Content-Type": "application/json", "User-Agent": "super-browser/1.0"}
    request_headers.update(headers)
    data = None if body is None else _json_dump(body).encode("utf-8")
    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        response = urlopen(request, timeout=timeout_seconds or 600)
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        if detail:
            raise HTTPError(exc.url, exc.code, f"{exc.reason}: {detail}", exc.headers, None) from None
        raise
    raw = response.read()
    if not raw:
        return {}
    import json

    return json.loads(raw.decode("utf-8"))


def _timeout_seconds(task: TaskSpec, default: int) -> int:
    if task.timeout_seconds is None:
        return default
    return max(1, int(task.timeout_seconds))


def _timeout_milliseconds(task: TaskSpec, default: int) -> int:
    if task.timeout_seconds is None:
        return default
    return max(1, int(task.timeout_seconds) * 1000)


def _safe_browser_close(browser) -> str | None:
    try:
        browser.close()
    except Exception as exc:
        return redact_text(str(exc))
    return None


def _payload_get(payload: dict, dotted_path: str):
    current = payload
    for key in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _payload_status_value(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("status", "state"):
        value = payload.get(key)
        if value is not None:
            return str(value).strip().lower()
    data = payload.get("data")
    if isinstance(data, dict):
        return _payload_status_value(data)
    return ""


def _provider_payload_failure_reason(payload: dict | None, provider_name: str) -> str | None:
    if not isinstance(payload, dict):
        return f"{provider_name} returned a non-object response"
    if not payload:
        return f"{provider_name} returned an empty response"
    explicit_error = _payload_explicit_error(payload)
    if explicit_error:
        return f"{provider_name} returned {explicit_error}"
    status_failure = _payload_status_failure(payload)
    if status_failure:
        return f"{provider_name} returned {status_failure}"
    boolean_failure = _payload_boolean_failure(payload)
    if boolean_failure:
        return f"{provider_name} returned {boolean_failure}"
    return None


def _provider_output_failure_reason(output: bytes | str | None, provider_name: str, empty_is_failure: bool = False) -> str | None:
    text = _output_text(output)
    if not text.strip():
        return f"{provider_name} returned empty output" if empty_is_failure else None
    payload = _json_payload_from_output(text)
    if payload is None:
        return None
    if isinstance(payload, dict):
        return _provider_payload_failure_reason(payload, provider_name)
    if isinstance(payload, list):
        for index, item in enumerate(payload):
            if isinstance(item, dict):
                failure_reason = _provider_payload_failure_reason(item, provider_name)
                if failure_reason:
                    return failure_reason.replace(f"{provider_name} returned ", f"{provider_name} returned item[{index}] ", 1)
        return None
    return None


def _output_text(output: bytes | str | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return str(output)


def _json_payload_from_output(text: str):
    import json

    try:
        return json.loads(text)
    except Exception:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except Exception:
            continue
    return None


def _payload_explicit_error(payload: dict, prefix: str = "") -> str | None:
    for key in ("error", "errors"):
        value = payload.get(key)
        if value:
            return f"{prefix}{key}: {_payload_value_summary(value)}"
    for child_key, child in _payload_provider_children(payload):
        failure_reason = _payload_explicit_error(child, f"{prefix}{child_key}.")
        if failure_reason:
            return failure_reason
    return None


def _payload_status_failure(payload: dict, prefix: str = "") -> str | None:
    for key in ("status", "state"):
        value = payload.get(key)
        if value is None:
            continue
        status = str(value).strip().lower()
        if status in FAILED_PROVIDER_STATUSES:
            return f"{prefix}{key}={status}"
        if status in UNFINISHED_PROVIDER_STATUSES:
            return f"unfinished {prefix}{key}={status}"
    for child_key, child in _payload_provider_children(payload):
        failure_reason = _payload_status_failure(child, f"{prefix}{child_key}.")
        if failure_reason:
            return failure_reason
    return None


def _payload_boolean_failure(payload: dict, prefix: str = "") -> str | None:
    for key in ("success", "ok"):
        if payload.get(key) is False:
            return f"{prefix}{key}=false"
    for child_key, child in _payload_provider_children(payload):
        failure_reason = _payload_boolean_failure(child, f"{prefix}{child_key}.")
        if failure_reason:
            return failure_reason
    return None


def _payload_provider_children(payload: dict) -> list[tuple[str, dict]]:
    children = []
    for key in ("data", "result", "results", "response", "output", "items", "pages"):
        value = payload.get(key)
        if isinstance(value, dict):
            children.append((key, value))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    children.append((f"{key}[{index}]", item))
    return children


def _payload_value_summary(value: Any) -> str:
    text = safe_json_dumps(value) if isinstance(value, (dict, list, tuple)) else str(value)
    text = redact_text(text)
    return text if len(text) <= 500 else text[:497] + "..."


def _orgo_chat_completions_url(api_base: str) -> str:
    base = api_base.rstrip("/")
    return f"{base}/v1/chat/completions" if base.endswith("/api") else f"{base}/chat/completions"


ORGO_DEFAULT_WORKSPACE_NAME = "super-browser"
ORGO_DEFAULT_COMPUTER_NAME = "super-browser-agent"
ORGO_AUTO_STOP_MINUTES = 30


def _orgo_collection(payload, keys: tuple[str, ...]) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for candidate in (*keys, "data", "items"):
            value = payload.get(candidate)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _orgo_resolve_computer_id(api_base: str, headers: dict[str, str], timeout_seconds: int) -> tuple[str, str]:
    """Resolve an Orgo computer id: pinned env, then reuse, then start, then create."""
    pinned = os.environ.get("ORGO_COMPUTER_ID")
    if pinned:
        return pinned, "pinned via ORGO_COMPUTER_ID"
    # Orgo's live API returns workspace lists under "projects" and computers under "desktops".
    workspaces = _orgo_collection(_http_json(f"{api_base}/workspaces", None, headers, method="GET", timeout_seconds=timeout_seconds), ("workspaces", "projects"))
    workspace = next((item for item in workspaces if item.get("name") == ORGO_DEFAULT_WORKSPACE_NAME), None)
    if workspace is None and workspaces:
        workspace = workspaces[0]
    if workspace is None:
        workspace = _http_json(f"{api_base}/workspaces", {"name": ORGO_DEFAULT_WORKSPACE_NAME}, headers, timeout_seconds=timeout_seconds)
    workspace_id = workspace["id"]
    detail = _http_json(f"{api_base}/workspaces/{workspace_id}", None, headers, method="GET", timeout_seconds=timeout_seconds)
    computers = _orgo_collection(detail, ("computers", "desktops"))
    running = [item for item in computers if item.get("status") == "running"]
    chosen = next((item for item in running if item.get("name") == ORGO_DEFAULT_COMPUTER_NAME), None)
    if chosen is None and running:
        chosen = running[0]
    if chosen is not None:
        return chosen["id"], f"reused running computer {chosen.get('name', chosen['id'])}"
    if computers:
        target = next((item for item in computers if item.get("name") == ORGO_DEFAULT_COMPUTER_NAME), computers[0])
        _http_json(f"{api_base}/computers/{target['id']}/start", {}, headers, timeout_seconds=timeout_seconds)
        return target["id"], f"started existing computer {target.get('name', target['id'])}"
    created = _http_json(
        f"{api_base}/computers",
        {"workspace_id": workspace_id, "name": ORGO_DEFAULT_COMPUTER_NAME, "auto_stop_minutes": ORGO_AUTO_STOP_MINUTES},
        headers,
        timeout_seconds=timeout_seconds,
    )
    return created["id"], f"created computer {ORGO_DEFAULT_COMPUTER_NAME} in workspace {workspace_id}"


def _task_prompt(task: TaskSpec) -> str:
    prompt = f"Start at {task.url}. {task.goal}" if task.url else task.goal
    if draft_only_for_goal(task.goal):
        return (
            "SAFETY: This is a draft-only run. Prepare, type, fill, or place the requested draft content only. "
            "Do not publish, post, comment, reply, respond, message, DM, send, submit, upload, click, tap, press, or activate any final external-write control. "
            "Do not follow, connect, like, react, vote, bookmark, save, pin, star, watch, fork, share, RSVP, attend, join, leave, request, invite, accept, decline, approve, schedule, buy, purchase, checkout, donate, bid, apply, register, subscribe, unsubscribe, report, block, mute, tag, endorse, review, rate, or change any external state. "
            "Do not update CRM records, change cart/order/payment/trading/banking/payout/legal/government/health/insurance/identity/project/repository/cloud-file/sharing/integration/settings/secrets/infrastructure/billing/workspace/channel/role/moderation state, toggle notifications, archive or mark messages/email, remove members, accept or decline invites, or change account/profile state. "
            "Stop before the final action.\n"
            f"{prompt}"
        )
    if _task_is_external_write(task):
        return (
            "SAFETY: This is an external-write run. Provider execution is allowed only after durable approval has been verified by the runtime. "
            "Perform only the exact requested external action on the exact intended target. "
            "Do not perform adjacent actions such as following, connecting, liking, reacting, sharing, saving, bookmarking, joining, inviting, messaging, buying, checking out, changing account settings, changing permissions, or updating unrelated CRM/project/workspace records. "
            "Do not expand the audience, target, content, account, payment, billing, legal, government, health, insurance, identity, infrastructure, repository, cloud-file, workspace, channel, moderation, or profile scope beyond the request. "
            "Stop after the exact requested action and preserve evidence for verification.\n"
            f"{prompt}"
        )
    if _task_is_authenticated_read(task):
        return (
            "SAFETY: This is an authenticated read/navigation run. Use the available session or credentials only for the requested read, navigation, extraction, or inspection. "
            "Do not publish, post, comment, reply, message, send, submit, upload, follow, connect, like, react, share, save, bookmark, join, leave, buy, checkout, change settings, update account/profile state, or mutate external records. "
            "If the site presents a final write/submit/confirm control, stop before activating it and report the blocker.\n"
            f"{prompt}"
        )
    return (
        "SAFETY: This is a read-only run. Navigate, search, scroll, inspect, and extract only. "
        "Do not publish, post, comment, reply, message, send, submit, upload, follow, connect, like, react, share, save, bookmark, join, leave, buy, checkout, change settings, update account/profile state, or mutate external records. "
        "Search/filter/navigation controls are allowed only when they do not create external state changes.\n"
        f"{prompt}"
    )


def _task_is_external_write(task: TaskSpec) -> bool:
    return bool(task.external_write or infer_risk(task.goal) == "external_write")


def _task_is_authenticated_read(task: TaskSpec) -> bool:
    return bool(task.requires_auth or infer_risk(task.goal) == "credential")


def _object_to_payload(value) -> dict:
    if isinstance(value, dict):
        return value
    payload = {}
    for key in (
        "output",
        "status",
        "state",
        "error",
        "errors",
        "message",
        "success",
        "ok",
        "id",
        "session_id",
        "sessionId",
        "liveUrl",
        "live_url",
        "recordingUrl",
        "recording_url",
        "screenshotUrl",
        "screenshot_url",
        "totalCostUsd",
        "total_cost_usd",
    ):
        if hasattr(value, key):
            payload[key] = getattr(value, key)
    if not payload:
        payload["repr"] = repr(value)
    return payload


def _event(event_type: str, reason: str) -> dict[str, str]:
    return {"at": utc_now(), "type": event_type, "reason": reason}


def _json_dump(payload: dict) -> str:
    import json

    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _redacted_json_dump(payload: object) -> str:
    return safe_json_dumps(payload)


def _write_redacted_output(path: Path, output: bytes | str | None) -> None:
    if output is None:
        path.write_text("", encoding="utf-8")
        return
    if isinstance(output, bytes):
        try:
            text = output.decode("utf-8")
        except UnicodeDecodeError:
            path.write_text("[binary output omitted by redaction policy]", encoding="utf-8")
            return
    else:
        text = output
    try:
        import json

        payload = json.loads(text)
    except Exception:
        path.write_text(redact_text(text) or "", encoding="utf-8")
        return
    path.write_text(_redacted_json_dump(payload), encoding="utf-8")


def _write_redacted_http_body(path: Path, body: bytes) -> dict[str, object]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        path.write_bytes(body)
        return {"saved_bytes": len(body), "body_encoding": "binary", "body_redacted": False}

    try:
        import json

        payload = json.loads(text)
    except Exception:
        redacted_text = redact_text(text) or ""
        body_redacted = redacted_text != text
    else:
        body_redacted = redact(payload) != payload
        redacted_text = _redacted_json_dump(payload)

    path.write_text(redacted_text, encoding="utf-8")
    return {
        "saved_bytes": len(redacted_text.encode("utf-8")),
        "body_encoding": "utf-8",
        "body_redacted": body_redacted,
    }
