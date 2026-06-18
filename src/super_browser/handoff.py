from __future__ import annotations

from typing import Any

from .providers import provider_readiness
from .redaction import redact
from .store import RunStore, summarize_run
from .verifier import verify_run_payload


PROVIDER_CONSTRAINT_FAILURE_TYPES = {
    "provider_constraint_invalid_task",
    "provider_constraint_unknown_provider",
    "provider_allowlist_violation",
    "provider_file_url_constraint_violation",
    "provider_cost_constraint_violation",
    "provider_target_scope_mismatch",
    "provider_missing_url_constraint_violation",
    "provider_raw_http_url_constraint_violation",
}
RUN_REPORT_STATUS_FAILURE_TYPES = {"status_mismatch"}
RUN_REPORT_EVIDENCE_FAILURE_TYPES = {
    "artifact_hash_mismatch",
    "invalid_run_id",
    "missing_artifact_path",
    "missing_run_report",
    "untrusted_artifact_path",
    "run_report_run_id_mismatch",
    "run_report_final_provider_not_planned",
    "run_report_complete_without_complete_attempt",
    "run_report_final_provider_attempt_mismatch",
    "run_report_final_provider_attempt_missing",
    "run_report_final_status_attempt_mismatch",
}
UNTRUSTED_APPROVAL_INTEGRITY_STATUSES = {
    "missing",
    "mismatch",
    "missing_fingerprint",
    "missing_approval_id",
    "missing_required_before",
    "invalid_required_before",
    "missing_decision_metadata",
    "unknown_status",
}


def build_handoff(run_id: str) -> dict[str, Any]:
    store = RunStore(create=False)
    run = store.get(run_id)
    if not run:
        raise ValueError(f"Run not found: {run_id}")

    plan = run.get("plan") or {}
    task = plan.get("task") or {}
    verification = verify_run_payload(run)
    policy_guard = verification.get("policy_guard") or {}
    provider_names = _provider_sequence(plan, verification)
    readiness = _selected_provider_readiness(provider_names)
    approval = _approval_state(run, plan, verification)
    resume = _resume_state(run, approval, verification)
    next_steps = _next_steps(run, plan, approval, verification)

    return redact(
        {
            "type": "super_browser_run_handoff",
            "run_id": run.get("run_id"),
            "status": run.get("status"),
            "summary": summarize_run(run),
            "task": {
                "goal": task.get("goal"),
                "url": task.get("url"),
                "target_scope": task.get("target_scope", "none"),
                "requires_auth": bool(policy_guard.get("requires_auth")),
                "anti_bot_risk": bool(task.get("anti_bot_risk")),
                "needs_desktop": bool(task.get("needs_desktop")),
                "external_write": bool(policy_guard.get("external_write")),
                "draft_only": bool(policy_guard.get("draft_only")),
                "raw_http": bool(task.get("raw_http")),
                "long_running": bool(policy_guard.get("long_running")),
                "timeout_seconds": task.get("timeout_seconds"),
            },
            "route": {
                "mode": plan.get("mode"),
                "primary_provider": plan.get("primary_provider"),
                "fallback_providers": plan.get("fallback_providers", []),
                "provider_sequence": provider_names,
                "missing_env": plan.get("missing_env", []),
                "approval_required": bool(policy_guard.get("approval_required")),
                "cost_estimate": plan.get("cost_estimate", {}),
                "timeout_seconds": task.get("timeout_seconds"),
                "planner_decision": (plan.get("council_report") or {}).get("planner_decision", {}),
            },
            "provider_readiness": readiness,
            "approval": approval,
            "resume": resume,
            "verification": _verification_summary(verification),
            "commands": _commands(run.get("run_id")),
            "mcp": _mcp_commands(run.get("run_id")),
            "docs": [
                "super-browser://SKILL",
                "super-browser://references/provider-matrix",
                "super-browser://references/routing-playbook",
                "super-browser://references/security-and-approval-policy",
                "super-browser://references/live-test-matrix",
            ],
            "agent_next_steps": next_steps,
        }
    )


def _provider_sequence(plan: dict[str, Any], verification: dict[str, Any]) -> list[str]:
    sequence = [plan.get("primary_provider"), *(plan.get("fallback_providers") or [])]
    selected = verification.get("selected_provider")
    if selected:
        sequence.append(selected)
    seen = set()
    return [name for name in sequence if isinstance(name, str) and not (name in seen or seen.add(name))]


def _selected_provider_readiness(provider_names: list[str]) -> list[dict[str, Any]]:
    wanted = set(provider_names)
    rows = []
    for row in provider_readiness():
        if row.get("name") not in wanted:
            continue
        rows.append(
            {
                "name": row.get("name"),
                "display_name": row.get("display_name"),
                "readiness_status": row.get("readiness_status"),
                "usable_now": row.get("usable_now"),
                "production_ready": row.get("production_ready"),
                "production_ready_scope": row.get("production_ready_scope"),
                "certified_workflow_classes": row.get("certified_workflow_classes", []),
                "stale_certified_workflow_classes": row.get("stale_certified_workflow_classes", []),
                "supported_live_workflow_classes": row.get("supported_live_workflow_classes", []),
                "uncertified_workflow_classes": row.get("uncertified_workflow_classes", []),
                "ignored_unsupported_evidence_workflow_classes": row.get("ignored_unsupported_evidence_workflow_classes", []),
                "ignored_provider_mismatch_evidence_workflow_classes": row.get("ignored_provider_mismatch_evidence_workflow_classes", []),
                "requires_live_test_before_production": row.get("requires_live_test_before_production"),
                "requires_live_test_before_broader_production": row.get("requires_live_test_before_broader_production"),
                "production_blockers": row.get("production_blockers", []),
                "missing_required_env": row.get("missing_required_env", []),
                "missing_optional_env": row.get("missing_optional_env", []),
                "next_action": row.get("next_action"),
                "latest_live_test": row.get("latest_live_test"),
            }
        )
    return rows


def _approval_state(run: dict[str, Any], plan: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    approvals = [item for item in run.get("approvals", []) if item.get("type") == "approval_request"]
    pending = [item for item in approvals if item.get("status") == "pending"]
    approved = [item for item in approvals if item.get("status") == "approved"]
    denied = [item for item in approvals if item.get("status") == "denied"]
    latest = approvals[-1] if approvals else None
    policy_guard = verification.get("policy_guard") or {}
    return {
        "required": bool(policy_guard.get("approval_required", plan.get("approval_required"))),
        "pending": bool(pending),
        "approved": bool(approved),
        "denied": bool(denied),
        "latest_request": latest,
        "next_action": _approval_next_action(run, pending, denied),
    }


def _approval_next_action(run: dict[str, Any], pending: list[dict[str, Any]], denied: list[dict[str, Any]]) -> str:
    if pending:
        request = pending[-1]
        return f"Review approval request {request.get('approval_id')} before provider execution."
    if denied:
        return "Approval was denied; do not execute this run unless a new run is created."
    if run.get("status") == "approved":
        return "Approval is recorded; resume or approve with execute only if the exact action is still intended."
    return "No approval action is pending."


def _resume_state(run: dict[str, Any], approval: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    status = run.get("status")
    plan_integrity = verification.get("plan_integrity", {})
    approval_integrity = verification.get("approval_integrity", {})
    approval_expiry = verification.get("approval_expiry", {})
    write_retry_guard = verification.get("write_retry_guard", {})
    policy_guard = verification.get("policy_guard", {})
    status_failure = _first_failure(verification, RUN_REPORT_STATUS_FAILURE_TYPES)
    evidence_failure = _first_failure(verification, RUN_REPORT_EVIDENCE_FAILURE_TYPES)
    if status_failure and _approved_retry_transition(status, write_retry_guard):
        status_failure = None
    if evidence_failure and _evidence_failure_allows_state_transition(run, write_retry_guard, evidence_failure):
        evidence_failure = None
    base = {
        "safe_to_resume": False,
        "will_execute_provider": False,
        "fresh_retry_approval_required": bool(write_retry_guard.get("fresh_retry_approval_required")),
    }
    if plan_integrity.get("status") in {"mismatch", "missing"}:
        return {**base, "reason": "run report plan integrity does not match the current run plan"}
    if status_failure:
        return {**base, "reason": "run report final_status does not match the current run status"}
    if _has_failure_type(verification, PROVIDER_CONSTRAINT_FAILURE_TYPES):
        return {**base, "reason": "provider sequence violates task constraints"}
    if evidence_failure:
        return {**base, "reason": "run-report or artifact evidence is missing or inconsistent"}
    if approval_integrity.get("status") in UNTRUSTED_APPROVAL_INTEGRITY_STATUSES:
        return {**base, "reason": "approval integrity does not match the current plan"}
    if approval_expiry.get("status") == "expired":
        return {
            **base,
            "safe_to_resume": True,
            "reason": "resume will create a fresh approval because the previous approval expired",
        }
    if policy_guard.get("non_resumable_safety_stop"):
        return {**base, "reason": "blocked run is a target-scope or DNS safety stop; create a new run or replan before provider execution"}
    if write_retry_guard.get("fresh_retry_approval_required"):
        return {
            **base,
            "safe_to_resume": True,
            "reason": "resume will create a fresh retry approval before another external-write provider attempt",
        }
    if status == "awaiting_approval":
        return {**base, "reason": "approval is still pending"}
    if status in {"denied", "complete"}:
        return {**base, "reason": f"run is {status}"}
    if status == "executing":
        return {**base, "reason": "run is already executing unless its lease later expires"}
    if approval.get("pending"):
        return {**base, "reason": "pending approval must be resolved first"}
    safe_to_resume = status in {"planned", "approved", "blocked", "failed"}
    return {
        **base,
        "safe_to_resume": safe_to_resume,
        "will_execute_provider": safe_to_resume,
        "reason": f"run status is {status}",
    }


def _verification_summary(verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": verification.get("status"),
        "confidence": verification.get("confidence"),
        "selected_provider": verification.get("selected_provider"),
        "checks": verification.get("checks", []),
        "failures": verification.get("failures", []),
        "artifact_count": len(verification.get("artifacts") or []),
        "run_report_path": verification.get("run_report_path"),
        "trace_links": verification.get("trace_links", []),
        "plan_integrity": verification.get("plan_integrity", {}),
        "run_id_integrity": verification.get("run_id_integrity", {}),
        "approval_integrity": verification.get("approval_integrity", {}),
        "approval_expiry": verification.get("approval_expiry", {}),
        "policy_guard": verification.get("policy_guard", {}),
        "write_retry_guard": verification.get("write_retry_guard", {}),
    }


def _next_steps(run: dict[str, Any], plan: dict[str, Any], approval: dict[str, Any], verification: dict[str, Any]) -> list[str]:
    steps = []
    if plan.get("missing_env"):
        steps.append("Configure missing env vars before using every planned provider: " + ", ".join(plan.get("missing_env", [])))
    if (verification.get("plan_integrity") or {}).get("status") in {"mismatch", "missing"}:
        steps.append("Do not resume this run as a provider retry; create a new run because run-report evidence no longer matches the current plan.")
    if _has_failure_type(verification, RUN_REPORT_STATUS_FAILURE_TYPES):
        steps.append("Do not resume this run as a provider retry; create a new run because run-report final_status no longer matches the saved run status.")
    if _has_failure_type(verification, RUN_REPORT_EVIDENCE_FAILURE_TYPES):
        steps.append("Do not resume this run as a provider retry; create a new run because run-report or artifact evidence is missing or inconsistent.")
    if _has_failure_type(verification, PROVIDER_CONSTRAINT_FAILURE_TYPES):
        steps.append("Do not resume this run; create a new run because the provider sequence violates task constraints.")
    if (verification.get("approval_integrity") or {}).get("status") in UNTRUSTED_APPROVAL_INTEGRITY_STATUSES:
        steps.append("Do not resume this run; create a new run because approval evidence no longer matches the current plan.")
    if (verification.get("approval_expiry") or {}).get("status") == "expired":
        steps.append("Resume will create a fresh approval before provider execution because the previous approval expired.")
    if ((verification.get("policy_guard") or {}).get("non_resumable_safety_stop")):
        steps.append("Do not resume this run; create a new run or replan because the block was a target-scope or DNS safety stop.")
    if (verification.get("write_retry_guard") or {}).get("fresh_retry_approval_required"):
        steps.append("Resume will create a fresh retry approval before another external-write provider attempt.")
    if approval.get("pending"):
        steps.append("Resolve the pending approval before execution.")
    if verification.get("failures"):
        steps.append("Fix verifier failures before trusting output.")
    status = run.get("status")
    if status == "planned":
        steps.append("Resume the run when ready to execute.")
    elif status == "approved":
        steps.append("Resume the approved run when ready to execute.")
    elif status == "complete":
        steps.append("Inspect verifier confidence, artifacts, and run-report before final user-facing claims.")
    elif status == "failed":
        steps.append("Review provider attempts and resume only if retry policy allows it.")
    elif status == "blocked":
        steps.append("Review blocked reason, missing env vars, and provider readiness before resuming.")
    if not steps:
        steps.append("Inspect the saved run, provider readiness, and verification summary before taking action.")
    return steps


def _has_failure_type(verification: dict[str, Any], failure_types: set[str]) -> bool:
    return bool(_first_failure(verification, failure_types))


def _first_failure(verification: dict[str, Any], failure_types: set[str]) -> dict | None:
    for failure in verification.get("failures", []):
        if failure.get("type") in failure_types:
            return failure
    return None


def _approved_retry_transition(status: str, write_retry_guard: dict[str, Any]) -> bool:
    return bool(status == "approved" and write_retry_guard.get("retry_approval_after_last_attempt"))


def _evidence_failure_allows_state_transition(run: dict[str, Any], write_retry_guard: dict[str, Any], failure: dict) -> bool:
    if failure.get("type") != "missing_run_report":
        return False
    if write_retry_guard.get("fresh_retry_approval_required"):
        return True
    return any(event.get("type") == "stale_execution_recovered" for event in run.get("events", []))


def _commands(run_id: str | None) -> dict[str, str]:
    run = run_id or "<run-id>"
    return {
        "get": f"super-browser get {run}",
        "handoff": f"super-browser handoff {run}",
        "resume": f"super-browser resume {run}",
        "verify": f"super-browser verify {run}",
        "approve": f"super-browser approve {run} --by <actor> --reason <reason>",
        "deny": f"super-browser deny {run} --by <actor> --reason <reason>",
        "doctor": "super-browser doctor",
    }


def _mcp_commands(run_id: str | None) -> dict[str, dict[str, Any]]:
    run = run_id or "<run-id>"
    return {
        "get_browser_run": {"run_id": run},
        "handoff_browser_run": {"run_id": run},
        "resume_browser_run": {"run_id": run},
        "verify_browser_run": {"run_id": run},
        "approve_browser_run": {"run_id": run, "by": "<actor>", "reason": "<reason>", "execute": False},
        "deny_browser_run": {"run_id": run, "by": "<actor>", "reason": "<reason>"},
        "browser_doctor": {},
    }
