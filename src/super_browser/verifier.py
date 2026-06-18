from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifacts import fingerprint_path
from .models import action_fingerprint_from_plan, plan_fingerprint
from .models import TaskSpec
from .policy import approval_required as task_approval_required, draft_only_for_goal, infer_risk, long_running_for_goal, requires_auth_for_goal
from .providers import PROVIDERS
from .redaction import redact, redact_text, safe_json_dumps
from .router import provider_sequence_constraint_failures, target_scope_for_url
from .store import RunStore, default_state_dir


CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
DEFAULT_APPROVAL_TTL_SECONDS = 30 * 60
SAFETY_EVENT_TYPES = {
    "blocked",
    "resume_blocked",
    "external_write_attempt_started",
    "external_write_retry_blocked",
    "approval_expired",
    "approval_granted",
    "approval_denied",
    "stale_execution_recovered",
}
APPROVAL_INTEGRITY_FAILURE_STATUSES = {
    "missing",
    "mismatch",
    "missing_fingerprint",
    "missing_approval_id",
    "missing_required_before",
    "invalid_required_before",
    "missing_decision_metadata",
    "unknown_status",
}
NON_RESUMABLE_SAFETY_REASONS = {
    "raw_http_redirect_target_scope",
    "raw_http_resolved_target_scope",
    "provider_url_resolved_target_scope",
    "browser_request_target_scope",
}
NON_RESUMABLE_SAFETY_ERROR_MARKERS = (
    "target dns could not be verified",
    "target url dns could not be verified",
    "resolved to a sensitive target scope",
    "redirect to sensitive target scope",
    "request to sensitive target scope was blocked",
    "public target dns resolution failed",
    "resolved target to sensitive scope was blocked",
    "resolved provider target to sensitive scope was blocked",
)
RUN_ID_PATTERN = re.compile(r"run_[A-Za-z0-9][A-Za-z0-9_-]*")


def verify_run(run_id: str) -> dict[str, Any]:
    store = RunStore(create=False)
    run = store.get(run_id)
    if not run:
        raise ValueError(f"Run not found: {run_id}")
    report = verify_run_payload(run)
    report_path = _write_verification_report(report)
    if report_path:
        report["verification_report_path"] = report_path
    return report


def verify_run_payload(run: dict[str, Any]) -> dict[str, Any]:
    verification = run.get("verification") or {}
    artifacts = run.get("artifacts", [])
    run_report_artifact = _latest_artifact(artifacts, "run_report")
    run_report = _read_json_artifact(run_report_artifact, run)
    path_checks = _artifact_path_checks(run, artifacts)
    scope_checks = _artifact_scope_checks(run, artifacts)
    failures = []
    failures.extend(_run_id_failures(run))
    failures.extend(scope_checks["untrusted_paths"])
    failures.extend(path_checks["missing_paths"])
    failures.extend(path_checks["hash_mismatches"])
    provider_constraint_failures = provider_sequence_constraint_failures(run.get("plan") or {})
    failures.extend(provider_constraint_failures)

    status = run.get("status", "unknown")
    if status in {"complete", "blocked", "failed"} and not run_report:
        failures.append({"type": "missing_run_report", "message": "Execution run is missing run-report.json"})
    if run_report and run_report.get("final_status") != status:
        failures.append(
            {
                "type": "status_mismatch",
                "message": f"Run status is {status}, but run-report final_status is {run_report.get('final_status')}",
            }
        )
    plan_integrity = _plan_integrity(run, run_report)
    if plan_integrity["status"] == "missing":
        failures.append({"type": "missing_plan_fingerprint", "message": "run-report.json does not include plan_sha256"})
    elif plan_integrity["status"] == "mismatch":
        failures.append(
            {
                "type": "plan_fingerprint_mismatch",
                "message": "Stored run plan does not match run-report.json plan_sha256",
                "expected_sha256": plan_integrity.get("expected_sha256"),
                "actual_sha256": plan_integrity.get("actual_sha256"),
            }
        )
    if run_report:
        failures.extend(_run_report_consistency_failures(run, run_report))

    checks = ["run record exists"]
    checks.extend(verification.get("checks", []))
    if run_report:
        checks.append("run-report.json parsed")
    if plan_integrity["status"] == "verified":
        checks.append("run-report plan fingerprint verified")
    if path_checks["existing_count"]:
        checks.append(f"artifact paths exist: {path_checks['existing_count']}")
    if path_checks["hash_checked_count"]:
        checks.append(f"artifact hashes verified: {path_checks['hash_checked_count']}")
    cost_estimate = _cost_estimate(run, verification, run_report)
    if cost_estimate.get("budget_status"):
        checks.append(f"budget status: {cost_estimate['budget_status']}")
    if provider_constraint_failures:
        checks.append("provider sequence constraints failed")
    if _pending_approval(run):
        checks.append("approval is pending and execution is stopped")
    if _pending_retry_approval(run):
        checks.append("external write retry is blocked pending fresh approval")
    if _approval_denied(run):
        checks.append("approval denial is recorded")
    approval_integrity = _approval_integrity(run)
    failures.extend(_approval_integrity_failures(approval_integrity))
    if approval_integrity["status"] == "verified":
        checks.append("approval fingerprints verified")
    elif approval_integrity["status"] in APPROVAL_INTEGRITY_FAILURE_STATUSES:
        checks.append("approval integrity failed")
    approval_expiry = _approval_expiry(run)
    if approval_expiry["status"] == "expired":
        failures.append(
            {
                "type": "approval_expired",
                "message": "Latest approval expired before provider execution",
                "approval_id": approval_expiry.get("approval_id"),
                "approved_at": approval_expiry.get("approved_at"),
                "age_seconds": approval_expiry.get("age_seconds"),
                "ttl_seconds": approval_expiry.get("ttl_seconds"),
            }
        )
        checks.append("approval expired before provider execution")
    policy_guard = _policy_guard_summary(run, run_report)
    if policy_guard["approval_status"] == "missing":
        failures.append(
            {
                "type": "missing_approval_record",
                "message": "Plan requires approval but no approval request record exists",
            }
        )
        checks.append("approval record missing for approval-required plan")
    if policy_guard["safety_events"]:
        checks.append(f"safety events surfaced: {len(policy_guard['safety_events'])}")
    if policy_guard["blocked_reasons"]:
        checks.append(f"blocked reasons surfaced: {len(policy_guard['blocked_reasons'])}")
    checks = _dedupe(checks)

    selected_provider = (
        verification.get("selected_provider")
        or (run_report or {}).get("final_provider")
        or run.get("plan", {}).get("primary_provider")
    )
    confidence = _confidence_for(status, verification.get("confidence"), failures, run_report, run)
    result = {
        "run_id": run["run_id"],
        "status": status,
        "confidence": confidence,
        "selected_provider": selected_provider,
        "primary_provider": run.get("plan", {}).get("primary_provider"),
        "fallback_providers": run.get("plan", {}).get("fallback_providers", []),
        "cost_band": PROVIDERS[selected_provider].cost_band if selected_provider in PROVIDERS else None,
        "cost_estimate": cost_estimate,
        "budget_status": cost_estimate.get("budget_status") if isinstance(cost_estimate, dict) else None,
        "checks": checks,
        "failures": failures,
        "attempts": verification.get("attempts") or (run_report or {}).get("attempts", []),
        "artifacts": artifacts,
        "artifact_audit": path_checks,
        "artifact_scope": scope_checks,
        "run_id_integrity": _run_id_integrity(run),
        "trace_links": _trace_links(artifacts),
        "plan_integrity": plan_integrity,
        "approval_integrity": approval_integrity,
        "approval_expiry": approval_expiry,
        "approvals": run.get("approvals", []),
        "policy_guard": policy_guard,
        "write_retry_guard": _write_retry_guard(run),
        "run_report": run_report,
        "run_report_path": _trusted_artifact_path(run_report_artifact, run),
    }
    return redact(result)


def _confidence_for(status: str, stored_confidence: str | None, failures: list[dict], run_report: dict | None, run: dict[str, Any]) -> str:
    if failures:
        return "low"
    if status in {"awaiting_approval", "denied"}:
        return "high" if (status == "denied" or _pending_approval(run)) else "medium"
    if status == "complete":
        if run_report:
            return _min_confidence(stored_confidence or "high", "high")
        return "medium"
    if status in {"planned", "approved"}:
        return "low"
    return stored_confidence or "low"


def _min_confidence(left: str, right: str) -> str:
    return left if CONFIDENCE_ORDER.get(left, 0) <= CONFIDENCE_ORDER.get(right, 0) else right


def _latest_artifact(artifacts: list[dict[str, Any]], artifact_type: str) -> dict[str, Any]:
    for artifact in reversed(artifacts):
        if artifact.get("type") == artifact_type:
            return artifact
    return {}


def _read_json_artifact(artifact: dict[str, Any], run: dict[str, Any]) -> dict | None:
    path = artifact.get("path")
    if not path or not _artifact_path_is_trusted(path, run):
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _artifact_path_checks(run: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    checked = []
    missing = []
    hash_mismatches = []
    hash_checked_count = 0
    for artifact in artifacts:
        path = artifact.get("path")
        if not path:
            continue
        trusted = _artifact_path_is_trusted(path, run)
        row = {"type": artifact.get("type"), "provider": artifact.get("provider"), "path": path, "trusted": trusted}
        if not trusted:
            row["exists"] = None
            checked.append(row)
            continue
        exists = Path(path).exists()
        row["exists"] = exists
        if exists and Path(path).is_file() and artifact.get("sha256"):
            hash_checked_count += 1
            fingerprint = fingerprint_path(path)
            row["sha256"] = fingerprint["sha256"]
            row["expected_sha256"] = artifact.get("sha256")
            row["hash_ok"] = fingerprint["sha256"] == artifact.get("sha256")
            if not row["hash_ok"]:
                hash_mismatches.append(
                    {
                        "type": "artifact_hash_mismatch",
                        "artifact_type": artifact.get("type"),
                        "path": path,
                        "expected_sha256": artifact.get("sha256"),
                        "actual_sha256": fingerprint["sha256"],
                    }
                )
        checked.append(row)
        if not exists:
            missing.append({"type": "missing_artifact_path", "artifact_type": artifact.get("type"), "path": path})
    return {
        "checked": checked,
        "existing_count": len([item for item in checked if item["exists"]]),
        "missing_paths": missing,
        "hash_checked_count": hash_checked_count,
        "hash_mismatches": hash_mismatches,
    }


def _artifact_scope_checks(run: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    checked = []
    untrusted = []
    expected_root = _expected_artifact_root(run)
    for artifact in artifacts:
        path = artifact.get("path")
        if not path:
            continue
        trusted = _artifact_path_is_trusted(path, run)
        row = {
            "type": artifact.get("type"),
            "provider": artifact.get("provider"),
            "path": path,
            "trusted": trusted,
            "expected_root": str(expected_root) if expected_root else None,
        }
        checked.append(row)
        if not trusted:
            untrusted.append(
                {
                    "type": "untrusted_artifact_path",
                    "message": "Artifact path is outside this run's expected artifact directory",
                    "artifact_type": artifact.get("type"),
                    "provider": artifact.get("provider"),
                    "path": path,
                    "expected_root": str(expected_root) if expected_root else None,
                }
            )
    return {"checked": checked, "untrusted_paths": untrusted}


def _run_id_failures(run: dict[str, Any]) -> list[dict[str, Any]]:
    integrity = _run_id_integrity(run)
    if integrity["status"] == "verified":
        return []
    return [
        {
            "type": "invalid_run_id",
            "message": "Run id is not a safe generated Super Saiyan Browser run id",
            "run_id": integrity.get("run_id"),
        }
    ]


def _run_id_integrity(run: dict[str, Any]) -> dict[str, Any]:
    run_id = str(run.get("run_id") or "")
    return {
        "status": "verified" if _run_id_is_valid(run_id) else "invalid",
        "run_id": run_id,
        "pattern": RUN_ID_PATTERN.pattern,
    }


def _trusted_artifact_path(artifact: dict[str, Any], run: dict[str, Any]) -> str | None:
    path = artifact.get("path")
    if not path or not _artifact_path_is_trusted(path, run):
        return None
    return path


def _artifact_path_is_trusted(path: str, run: dict[str, Any]) -> bool:
    expected_root = _expected_artifact_root(run)
    if expected_root is None:
        return False
    try:
        resolved = Path(path).resolve(strict=False)
        resolved.relative_to(expected_root)
        return True
    except (OSError, ValueError):
        return False


def _expected_artifact_root(run: dict[str, Any]) -> Path | None:
    run_id = str(run.get("run_id") or "")
    if not _run_id_is_valid(run_id):
        return None
    return (default_state_dir() / "artifacts" / run_id).resolve(strict=False)


def _run_id_is_valid(run_id: str) -> bool:
    return bool(RUN_ID_PATTERN.fullmatch(run_id))


def _trace_links(artifacts: list[dict[str, Any]]) -> list[dict[str, str]]:
    links = []
    for artifact in artifacts:
        url = artifact.get("url")
        if url:
            links.append({"type": artifact.get("type", "trace"), "provider": artifact.get("provider", ""), "url": redact_text(url)})
    return links


def _cost_estimate(run: dict[str, Any], verification: dict[str, Any], run_report: dict | None) -> dict[str, Any]:
    if isinstance(verification.get("cost_estimate"), dict):
        return verification["cost_estimate"]
    if run_report and isinstance(run_report.get("cost_estimate"), dict):
        return run_report["cost_estimate"]
    plan = run.get("plan") or {}
    return plan.get("cost_estimate") if isinstance(plan.get("cost_estimate"), dict) else {}


def _run_report_consistency_failures(run: dict[str, Any], run_report: dict) -> list[dict[str, Any]]:
    failures = []
    run_id = run.get("run_id")
    report_run_id = run_report.get("run_id")
    final_status = run_report.get("final_status")
    final_provider = run_report.get("final_provider")
    attempts = run_report.get("attempts") if isinstance(run_report.get("attempts"), list) else []
    planned_sequence = _planned_provider_sequence(run.get("plan") or {})
    if report_run_id != run_id:
        failures.append(
            {
                "type": "run_report_run_id_mismatch",
                "message": "run-report run_id does not match the stored run id",
                "run_id": run_id,
                "run_report_run_id": report_run_id,
            }
        )
    if final_provider and planned_sequence and final_provider not in planned_sequence:
        failures.append(
            {
                "type": "run_report_final_provider_not_planned",
                "message": "run-report final_provider is not in the stored provider sequence",
                "final_provider": final_provider,
                "planned_providers": planned_sequence,
            }
        )
    if final_status == "complete":
        complete_attempts = [attempt for attempt in attempts if attempt.get("status") == "complete"]
        matching_attempts = [attempt for attempt in complete_attempts if attempt.get("provider") == final_provider]
        if not complete_attempts:
            failures.append(
                {
                    "type": "run_report_complete_without_complete_attempt",
                    "message": "run-report final_status is complete but no provider attempt completed",
                    "final_provider": final_provider,
                    "attempt_count": len(attempts),
                }
            )
        elif final_provider and not matching_attempts:
            failures.append(
                {
                    "type": "run_report_final_provider_attempt_mismatch",
                    "message": "run-report final_provider does not match any completed attempt",
                    "final_provider": final_provider,
                    "completed_providers": [attempt.get("provider") for attempt in complete_attempts],
                }
            )
    elif final_status in {"failed", "blocked"} and attempts:
        final_provider_attempts = [attempt for attempt in attempts if attempt.get("provider") == final_provider]
        if final_provider and not final_provider_attempts:
            failures.append(
                {
                    "type": "run_report_final_provider_attempt_missing",
                    "message": "run-report final_provider does not appear in attempts",
                    "final_provider": final_provider,
                    "attempt_providers": [attempt.get("provider") for attempt in attempts],
                }
            )
        elif final_provider_attempts and final_provider_attempts[-1].get("status") != final_status:
            failures.append(
                {
                    "type": "run_report_final_status_attempt_mismatch",
                    "message": "run-report final_status does not match the final provider attempt status",
                    "final_provider": final_provider,
                    "final_status": final_status,
                    "attempt_status": final_provider_attempts[-1].get("status"),
                }
            )
    return failures


def _planned_provider_sequence(plan: dict[str, Any]) -> list[str]:
    sequence = []
    primary = plan.get("primary_provider")
    if primary:
        sequence.append(primary)
    for provider in plan.get("fallback_providers", []) or []:
        if provider not in sequence:
            sequence.append(provider)
    return sequence


def _plan_integrity(run: dict[str, Any], run_report: dict | None) -> dict[str, Any]:
    if not run_report:
        return {"status": "unavailable", "expected_sha256": None, "actual_sha256": None}
    expected = run_report.get("plan_sha256")
    actual = plan_fingerprint(run.get("plan") or {})
    if not expected:
        return {"status": "missing", "expected_sha256": None, "actual_sha256": actual}
    if expected != actual:
        return {"status": "mismatch", "expected_sha256": expected, "actual_sha256": actual}
    return {"status": "verified", "expected_sha256": expected, "actual_sha256": actual}


def _pending_approval(run: dict[str, Any]) -> bool:
    return any(item.get("type") == "approval_request" and item.get("status") == "pending" for item in run.get("approvals", []))


def _approval_denied(run: dict[str, Any]) -> bool:
    return any(item.get("type") == "approval_request" and item.get("status") == "denied" for item in run.get("approvals", []))


def _pending_retry_approval(run: dict[str, Any]) -> bool:
    return any(
        item.get("type") == "approval_request" and item.get("status") == "pending" and item.get("required_before") == "provider_retry"
        for item in run.get("approvals", [])
    )


def _write_retry_guard(run: dict[str, Any]) -> dict[str, Any]:
    plan = run.get("plan") or {}
    task = plan.get("task") if isinstance(plan.get("task"), dict) else {}
    attempts = [event for event in run.get("events", []) if event.get("type") == "external_write_attempt_started"]
    retry_blocks = [event for event in run.get("events", []) if event.get("type") == "external_write_retry_blocked"]
    pending_retry = [
        item
        for item in run.get("approvals", [])
        if item.get("type") == "approval_request"
        and item.get("status") == "pending"
        and item.get("required_before") == "provider_retry"
    ]
    last_attempt_at = attempts[-1].get("at") if attempts else None
    retry_approval_after_last_attempt = _approved_retry_after(run, last_attempt_at)
    fresh_retry_approval_required = bool(
        _external_write_for_task(task)
        and attempts
        and run.get("status") in {"failed", "blocked", "approved", "planned"}
        and not pending_retry
        and not retry_approval_after_last_attempt
    )
    return {
        "external_write_attempt_count": len(attempts),
        "retry_block_count": len(retry_blocks),
        "pending_retry_approval": bool(pending_retry),
        "fresh_retry_approval_required": fresh_retry_approval_required,
        "retry_approval_after_last_attempt": retry_approval_after_last_attempt,
        "last_action_fingerprint": attempts[-1].get("action_fingerprint") if attempts else None,
    }


def _approved_retry_after(run: dict[str, Any], attempt_at: str | None) -> bool:
    if not attempt_at:
        return False
    for item in reversed(run.get("approvals", [])):
        if item.get("type") != "approval_request" or item.get("status") != "approved":
            continue
        if item.get("required_before") != "provider_retry":
            continue
        decided_at = item.get("decided_at")
        if decided_at and decided_at > attempt_at:
            return True
    return False


def _policy_guard_summary(run: dict[str, Any], run_report: dict | None) -> dict[str, Any]:
    plan = run.get("plan") or {}
    task = plan.get("task") if isinstance(plan.get("task"), dict) else {}
    approval_required = _approval_required_for_plan(plan)
    safety_events = _safety_events(run)
    blocked_reasons = _blocked_reasons(run, run_report)
    non_resumable = _non_resumable_safety_stop(blocked_reasons)
    return {
        "target_scope": _target_scope_for_task(task),
        "approval_required": approval_required,
        "approval_status": _approval_status(run, approval_required),
        "external_write": _external_write_for_task(task),
        "requires_auth": _requires_auth_for_task(task),
        "draft_only": _draft_only_for_task(task),
        "long_running": _long_running_for_task(task),
        "safety_stop": bool(run.get("status") in {"awaiting_approval", "denied"} or blocked_reasons),
        "non_resumable_safety_stop": bool(non_resumable),
        "non_resumable_reason": non_resumable.get("reason") if non_resumable else None,
        "safety_events": safety_events,
        "blocked_reasons": blocked_reasons,
        "write_retry_guard": _write_retry_guard(run),
    }


def _safety_events(run: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    for event in run.get("events", []):
        event_type = event.get("type")
        if event_type not in SAFETY_EVENT_TYPES:
            continue
        events.append(
            {
                "type": event_type,
                "reason": event.get("reason"),
                "provider": event.get("provider"),
                "status": event.get("status"),
                "by": event.get("by"),
                "action_fingerprint": event.get("action_fingerprint"),
                "at": event.get("at"),
            }
        )
    return events


def _blocked_reasons(run: dict[str, Any], run_report: dict | None) -> list[dict[str, Any]]:
    reasons = []
    for event in run.get("events", []):
        if event.get("type") in {"blocked", "resume_blocked", "external_write_retry_blocked"}:
            reasons.append(
                {
                    "source": "event",
                    "type": event.get("type"),
                    "reason": event.get("reason"),
                    "provider": event.get("provider"),
                    "at": event.get("at"),
                }
            )
    for attempt in (run_report or {}).get("attempts", []):
        if attempt.get("status") in {"blocked", "failed"}:
            reasons.append(
                {
                    "source": "attempt",
                    "type": attempt.get("status"),
                    "reason": attempt.get("error"),
                    "provider": attempt.get("provider"),
                    "order": attempt.get("order"),
                }
            )
    return _dedupe_dicts(reasons)


def _non_resumable_safety_stop(blocked_reasons: list[dict[str, Any]]) -> dict[str, Any] | None:
    for reason in blocked_reasons:
        if _blocked_reason_is_non_resumable(reason):
            return reason
    return None


def _blocked_reason_is_non_resumable(reason: dict[str, Any]) -> bool:
    value = str(reason.get("reason") or "").lower()
    if value in NON_RESUMABLE_SAFETY_REASONS:
        return True
    return any(marker in value for marker in NON_RESUMABLE_SAFETY_ERROR_MARKERS)


def _approval_status(run: dict[str, Any], approval_required: bool) -> str:
    approvals = [item for item in run.get("approvals", []) if item.get("type") == "approval_request"]
    if not approvals:
        return "missing" if approval_required else "not_required"
    if any(item.get("status") == "pending" for item in approvals):
        return "pending"
    latest = approvals[-1]
    if latest.get("status") in {"approved", "denied"}:
        return str(latest.get("status"))
    return "unknown"


def _approval_integrity(run: dict[str, Any]) -> dict[str, Any]:
    plan = run.get("plan") or {}
    approvals = [item for item in run.get("approvals", []) if item.get("type") == "approval_request"]
    approval_required = _approval_required_for_plan(plan)
    current_plan_sha256 = plan_fingerprint(plan)
    current_action_fingerprint = action_fingerprint_from_plan(plan)
    if not approvals:
        return {
            "status": "missing" if approval_required else "not_required",
            "approval_id": None,
            "approval_status": None,
            "required_before": None,
            "plan_matches": None,
            "action_matches": None,
            "approved_plan_sha256": None,
            "current_plan_sha256": current_plan_sha256,
            "approved_action_fingerprint": None,
            "current_action_fingerprint": current_action_fingerprint,
        }
    latest = approvals[-1]
    approval_status = latest.get("status")
    approval_id = latest.get("approval_id")
    required_before = latest.get("required_before")
    approved_plan_sha256 = latest.get("plan_sha256")
    approved_action_fingerprint = latest.get("action_fingerprint")
    plan_matches = approved_plan_sha256 == current_plan_sha256
    action_matches = approved_action_fingerprint == current_action_fingerprint
    valid_required_before = required_before in {"provider_execution", "provider_retry"}
    decision_metadata_present = bool(latest.get("decided_at") and latest.get("decided_by"))
    if approval_status in {"pending", "approved"}:
        if not approval_id:
            status = "missing_approval_id"
        elif not required_before:
            status = "missing_required_before"
        elif not valid_required_before:
            status = "invalid_required_before"
        elif not approved_plan_sha256 or not approved_action_fingerprint:
            status = "missing_fingerprint"
        elif not plan_matches or not action_matches:
            status = "mismatch"
        elif approval_status == "approved" and not decision_metadata_present:
            status = "missing_decision_metadata"
        else:
            status = "verified"
    elif approval_status == "denied":
        if not approval_id:
            status = "missing_approval_id"
        elif not required_before:
            status = "missing_required_before"
        elif not valid_required_before:
            status = "invalid_required_before"
        elif not approved_plan_sha256 or not approved_action_fingerprint:
            status = "missing_fingerprint"
        elif not plan_matches or not action_matches:
            status = "mismatch"
        elif not decision_metadata_present:
            status = "missing_decision_metadata"
        else:
            status = "denied"
    else:
        status = "unknown_status"
    return {
        "status": status,
        "approval_id": approval_id,
        "approval_status": approval_status,
        "required_before": required_before,
        "plan_matches": plan_matches,
        "action_matches": action_matches,
        "approval_id_present": bool(approval_id),
        "required_before_valid": valid_required_before,
        "decision_metadata_present": decision_metadata_present if approval_status in {"approved", "denied"} else None,
        "approved_plan_sha256": approved_plan_sha256,
        "current_plan_sha256": current_plan_sha256,
        "approved_action_fingerprint": approved_action_fingerprint,
        "current_action_fingerprint": current_action_fingerprint,
    }


def _approval_integrity_failures(integrity: dict[str, Any]) -> list[dict[str, Any]]:
    if integrity["status"] == "mismatch":
        return [
            {
                "type": "approval_integrity_mismatch",
                "message": "Latest approval request does not match the current run plan",
                "approval_id": integrity.get("approval_id"),
                "plan_matches": integrity.get("plan_matches"),
                "action_matches": integrity.get("action_matches"),
            }
        ]
    if integrity["status"] == "missing_fingerprint":
        return [
            {
                "type": "approval_integrity_missing_fingerprint",
                "message": "Latest pending or approved approval request is missing plan/action fingerprint evidence",
                "approval_id": integrity.get("approval_id"),
            }
        ]
    if integrity["status"] == "unknown_status":
        return [
            {
                "type": "approval_integrity_unknown_status",
                "message": "Latest approval request has an unknown status",
                "approval_id": integrity.get("approval_id"),
                "approval_status": integrity.get("approval_status"),
            }
        ]
    if integrity["status"] == "missing_approval_id":
        return [
            {
                "type": "approval_integrity_missing_approval_id",
                "message": "Latest approval request is missing an approval id",
            }
        ]
    if integrity["status"] == "missing_required_before":
        return [
            {
                "type": "approval_integrity_missing_required_before",
                "message": "Latest approval request is missing the required_before stage",
                "approval_id": integrity.get("approval_id"),
            }
        ]
    if integrity["status"] == "invalid_required_before":
        return [
            {
                "type": "approval_integrity_invalid_required_before",
                "message": "Latest approval request has an unsupported required_before stage",
                "approval_id": integrity.get("approval_id"),
                "required_before": integrity.get("required_before"),
            }
        ]
    if integrity["status"] == "missing_decision_metadata":
        return [
            {
                "type": "approval_integrity_missing_decision_metadata",
                "message": "Latest approved or denied approval request is missing decision metadata",
                "approval_id": integrity.get("approval_id"),
            }
        ]
    return []


def _approval_expiry(run: dict[str, Any]) -> dict[str, Any]:
    plan = run.get("plan") or {}
    if run.get("status") != "approved" or not _approval_required_for_plan(plan):
        return {
            "status": "not_applicable",
            "approval_id": None,
            "approval_status": None,
            "approved_at": None,
            "age_seconds": None,
            "ttl_seconds": _approval_ttl_seconds(),
        }
    approval = _latest_approval_with_status(run, "approved")
    if not approval:
        return {
            "status": "missing_approved_record",
            "approval_id": None,
            "approval_status": None,
            "approved_at": None,
            "age_seconds": None,
            "ttl_seconds": _approval_ttl_seconds(),
        }
    ttl_seconds = _approval_ttl_seconds()
    approved_at = approval.get("decided_at")
    decided_at = _parse_utc_datetime(approved_at)
    if decided_at is None:
        return {
            "status": "unknown",
            "approval_id": approval.get("approval_id"),
            "approval_status": approval.get("status"),
            "approved_at": approved_at,
            "age_seconds": None,
            "ttl_seconds": ttl_seconds,
        }
    age_seconds = max(0, int((datetime.now(timezone.utc) - decided_at).total_seconds()))
    return {
        "status": "expired" if age_seconds > ttl_seconds else "fresh",
        "approval_id": approval.get("approval_id"),
        "approval_status": approval.get("status"),
        "approved_at": approved_at,
        "age_seconds": age_seconds,
        "ttl_seconds": ttl_seconds,
        "required_before": approval.get("required_before"),
    }


def _latest_approval_with_status(run: dict[str, Any], status: str) -> dict[str, Any] | None:
    for item in reversed(run.get("approvals", [])):
        if item.get("type") == "approval_request" and item.get("status") == status:
            return item
    return None


def _approval_ttl_seconds() -> int:
    configured = os.environ.get("SUPER_BROWSER_APPROVAL_TTL_SECONDS")
    if configured is not None:
        try:
            return max(1, int(configured))
        except ValueError:
            return DEFAULT_APPROVAL_TTL_SECONDS
    return DEFAULT_APPROVAL_TTL_SECONDS


def _parse_utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _approval_required_for_plan(plan: dict[str, Any]) -> bool:
    if bool(plan.get("approval_required")):
        return True
    task = plan.get("task") if isinstance(plan.get("task"), dict) else {}
    try:
        parsed = TaskSpec(**task)
        parsed.target_scope = _target_scope_for_task(task)
        return task_approval_required(parsed)
    except (TypeError, ValueError):
        return False


def _target_scope_for_task(task: dict[str, Any]) -> str:
    try:
        return target_scope_for_url(task.get("url"))
    except Exception:
        return str(task.get("target_scope", "none"))


def _external_write_for_task(task: dict[str, Any]) -> bool:
    goal = str(task.get("goal", "") or "")
    try:
        return bool(task.get("external_write") or infer_risk(goal) in {"external_write", "destructive"})
    except Exception:
        return bool(task.get("external_write"))


def _requires_auth_for_task(task: dict[str, Any]) -> bool:
    goal = str(task.get("goal", "") or "")
    try:
        return bool(task.get("requires_auth") or requires_auth_for_goal(goal))
    except Exception:
        return bool(task.get("requires_auth"))


def _draft_only_for_task(task: dict[str, Any]) -> bool:
    goal = str(task.get("goal", "") or "")
    try:
        return draft_only_for_goal(goal) if goal else bool(task.get("draft_only"))
    except Exception:
        return bool(task.get("draft_only"))


def _long_running_for_task(task: dict[str, Any]) -> bool:
    goal = str(task.get("goal", "") or "")
    try:
        return bool(task.get("long_running") or long_running_for_goal(goal))
    except Exception:
        return bool(task.get("long_running"))


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _dedupe_dicts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    deduped = []
    for value in values:
        key = safe_json_dumps(value)
        if key not in seen:
            seen.add(key)
            deduped.append(value)
    return deduped


def _write_verification_report(report: dict[str, Any]) -> str | None:
    run_report_path = report.get("run_report_path")
    if not run_report_path:
        return None
    path = Path(run_report_path).parent / "verification-report.json"
    try:
        report["verification_report_path"] = str(path)
        path.write_text(safe_json_dumps(report), encoding="utf-8")
        return str(path)
    except Exception:
        return None
