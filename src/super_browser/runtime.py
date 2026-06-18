from __future__ import annotations

import os
from datetime import datetime, timezone

from .artifacts import annotate_artifact
from .adapters import execute_plan
from .models import ExecutionResult, Plan, RunState, action_fingerprint_from_plan, approval_request_from_plan, plan_fingerprint, utc_now
from .policy import approval_required as task_approval_required, infer_risk, long_running_for_goal
from .redaction import redact, redact_text, safe_json_dumps
from .router import build_plan, infer_task
from .store import RunStore, default_state_dir


DEFAULT_EXECUTION_LEASE_SECONDS = 4 * 60 * 60
LONG_RUNNING_EXECUTION_LEASE_SECONDS = 24 * 60 * 60
DEFAULT_APPROVAL_TTL_SECONDS = 30 * 60
UNTRUSTED_RUN_REPORT_PLAN_INTEGRITY = {"mismatch", "missing"}
UNTRUSTED_RUN_REPORT_STATUS_FAILURE_TYPES = {"status_mismatch"}
UNTRUSTED_RUN_REPORT_EVIDENCE_FAILURE_TYPES = {
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
UNTRUSTED_APPROVAL_INTEGRITY = {
    "missing",
    "mismatch",
    "missing_fingerprint",
    "missing_approval_id",
    "missing_required_before",
    "invalid_required_before",
    "missing_decision_metadata",
    "unknown_status",
}
UNTRUSTED_PROVIDER_CONSTRAINT_FAILURE_TYPES = {
    "provider_constraint_invalid_task",
    "provider_constraint_unknown_provider",
    "provider_allowlist_violation",
    "provider_file_url_constraint_violation",
    "provider_cost_constraint_violation",
    "provider_target_scope_mismatch",
    "provider_missing_url_constraint_violation",
    "provider_raw_http_url_constraint_violation",
    "provider_profile_missing",
    "provider_profile_constraint_violation",
    "provider_proxy_constraint_violation",
}
DURABLE_ARTIFACT_TYPES = {"plan"}


def create_run(
    goal: str,
    url: str | None = None,
    optimize: str = "balanced",
    execute: bool = True,
    providers_allowed: list[str] | None = None,
    max_cost_usd: float | None = None,
    timeout_seconds: int | None = None,
    profile: str | None = None,
    proxy: str | None = None,
    fleet_index: int | None = None,
    deliberation_rounds: int | None = None,
) -> RunState:
    task = infer_task(
        goal,
        url=url,
        optimize=optimize,
        providers_allowed=providers_allowed,
        max_cost_usd=max_cost_usd,
        timeout_seconds=timeout_seconds,
        profile=profile,
        proxy=proxy,
        fleet_index=fleet_index,
    )
    plan = build_plan(task, deliberation_rounds=deliberation_rounds)
    status = "awaiting_approval" if plan.approval_required else "planned"
    run = RunState.create(plan, status=status)
    run.artifacts.append({"type": "plan", "provider": plan.primary_provider})
    if plan.approval_required:
        run.approvals.append(approval_request_from_plan(plan))
    store = RunStore()
    store.save(run)
    deliberation_complete = plan.council_report.get("deliberation_complete", True)
    if execute and not plan.approval_required and deliberation_complete:
        run = _execute_run(run, store, "execution_started")
    elif execute and not deliberation_complete:
        run.events.append(
            {
                "at": utc_now(),
                "type": "execution_deferred",
                "reason": "deliberation_incomplete",
            }
        )
        store.save(run)
    return run


def approve_run(run_id: str, approver: str = "user", reason: str = "", execute: bool = False) -> RunState:
    approver = _validate_decision_actor(approver, "approval")
    reason = _validate_decision_reason(reason, "approval")
    store = RunStore()
    payload = store.get(run_id)
    if not payload:
        raise ValueError(f"Run not found: {run_id}")
    run = _run_from_payload(payload)
    if run.status != "awaiting_approval":
        raise ValueError(f"Run {run_id} is not awaiting approval; current status is {run.status}")
    plan = _plan_from_run(run)
    _validate_pending_approval_matches_plan(run, plan)
    _close_pending_approval(run, "approved", approver, reason)
    run.status = "approved"
    run.events.append({"at": utc_now(), "type": "approval_granted", "by": approver, "reason": redact_text(reason)})
    store.save(run)
    if execute:
        return _execute_run(run, store, "execution_started_after_approval")
    return run


def resume_run(run_id: str) -> RunState:
    store = RunStore()
    payload = store.get(run_id)
    if not payload:
        raise ValueError(f"Run not found: {run_id}")
    run = _run_from_payload(payload)
    if run.status == "awaiting_approval":
        run.events.append({"at": utc_now(), "type": "resume_blocked", "reason": "awaiting_approval"})
        run.verification = {"confidence": "high", "checks": ["resume stopped because approval is still pending"]}
        store.save(run)
        return run
    integrity_blocked = _block_untrusted_resume_evidence(run, store)
    if integrity_blocked:
        return integrity_blocked
    if run.status == "executing":
        plan = _plan_from_run(run)
        recovered = store.recover_stale_execution(
            run.run_id,
            lease_seconds=_execution_lease_seconds(plan),
            events=[{"at": utc_now(), "type": "stale_execution_recovered", "reason": "execution_lease_expired"}],
        )
        if recovered:
            return _execute_run(_run_from_payload(recovered), store, "execution_resumed_after_stale")
        run.events.append({"at": utc_now(), "type": "resume_noop", "status": run.status, "reason": "execution_lease_active"})
        run.verification = {"confidence": "medium", "checks": ["run is already executing and its execution lease is still active"]}
        store.save(run)
        return run
    if run.status in ("denied", "complete"):
        run.events.append({"at": utc_now(), "type": "resume_noop", "status": run.status})
        store.save(run)
        return run
    if run.status in ("planned", "approved", "blocked", "failed"):
        return _execute_run(run, store, "execution_resumed")
    run.events.append({"at": utc_now(), "type": "resume_noop", "status": run.status})
    store.save(run)
    return run


def deny_run(run_id: str, denied_by: str = "user", reason: str = "") -> RunState:
    denied_by = _validate_decision_actor(denied_by, "denial")
    reason = _validate_decision_reason(reason, "denial")
    store = RunStore()
    payload = store.get(run_id)
    if not payload:
        raise ValueError(f"Run not found: {run_id}")
    run = _run_from_payload(payload)
    if run.status != "awaiting_approval":
        raise ValueError(f"Run {run_id} is not awaiting approval; current status is {run.status}")
    _close_pending_approval(run, "denied", denied_by, reason)
    run.status = "denied"
    run.events.append({"at": utc_now(), "type": "approval_denied", "by": denied_by, "reason": redact_text(reason)})
    run.verification = {"confidence": "high", "checks": ["external write was stopped", "approval was denied"]}
    store.save(run)
    return run


def _execute_run(run: RunState, store: RunStore, event_type: str) -> RunState:
    integrity_blocked = _block_untrusted_resume_evidence(run, store)
    if integrity_blocked:
        return integrity_blocked
    plan = _plan_from_run(run)
    expired_approval_blocked = _block_expired_approval(run, plan, store)
    if expired_approval_blocked:
        return expired_approval_blocked
    retry_blocked = _block_duplicate_external_write_retry(run, plan, store)
    if retry_blocked:
        return retry_blocked
    claim_events = [{"at": utc_now(), "type": event_type, "provider": plan.primary_provider}]
    external_write_attempt = _external_write_attempt_event(run, plan)
    if external_write_attempt:
        claim_events.append(external_write_attempt)
    claimed = store.claim_execution(run.run_id, run.status, claim_events, lease_seconds=_execution_lease_seconds(plan))
    if not claimed:
        current = store.get(run.run_id)
        return _run_from_payload(current) if current else run
    run = _run_from_payload(claimed)
    try:
        result = execute_plan(plan, run.run_id, state_dir=default_state_dir(), approval_context=_execution_approval_context(run, plan))
    except Exception as exc:
        result = _runtime_execution_exception_result(plan, run.run_id, exc)
    result = _ensure_execution_result_run_report(plan, run.run_id, result)
    run.artifacts = _durable_artifacts(run.artifacts)
    run.artifacts.extend(redact(result.artifacts))
    run.events.extend(redact(result.events))
    run.verification = redact(result.verification)
    if result.error:
        run.events.append({"at": utc_now(), "type": "execution_error", "provider": result.provider, "message": redact_text(result.error)})
    run.status = result.status
    run.execution_lease = {}
    store.save(run)
    return run


def _ensure_execution_result_run_report(plan: Plan, run_id: str, result: ExecutionResult) -> ExecutionResult:
    if any(artifact.get("type") == "run_report" for artifact in result.artifacts):
        return result
    artifacts = list(result.artifacts)
    verification = dict(result.verification or {})
    checks = list(verification.get("checks", []))
    checks.append("run-report.json synthesized by runtime")
    attempt = {
        "order": 1,
        "provider": result.provider,
        "status": result.status,
        "error": redact_text(result.error),
        "artifact_count": len(artifacts),
        "verification": redact(verification),
    }
    attempts = verification.get("attempts") if isinstance(verification.get("attempts"), list) else [attempt]
    verification.update(
        {
            "checks": checks,
            "selected_provider": verification.get("selected_provider", result.provider),
            "attempts": attempts,
            "cost_estimate": verification.get("cost_estimate", plan.cost_estimate),
        }
    )
    try:
        artifact_dir = default_state_dir() / "artifacts" / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        report_path = artifact_dir / "run-report.json"
        report = {
            "run_id": run_id,
            "plan_sha256": plan_fingerprint(plan),
            "primary_provider": plan.primary_provider,
            "fallback_providers": plan.fallback_providers,
            "final_provider": result.provider,
            "final_status": result.status,
            "final_error": result.error,
            "attempts": attempts,
            "artifact_count": len(artifacts),
            "artifacts": artifacts,
            "cost_estimate": plan.cost_estimate,
            "verification": verification,
        }
        report_path.write_text(safe_json_dumps(report), encoding="utf-8")
        artifacts.append(annotate_artifact({"type": "run_report", "path": str(report_path), "provider": result.provider, "attempts": len(attempts)}))
        verification["run_report"] = str(report_path)
    except Exception as exc:
        verification.setdefault("failures", []).append(
            {
                "type": "runtime_run_report_write_failed",
                "provider": result.provider,
                "error_type": exc.__class__.__name__,
                "message": redact_text(str(exc)) or "",
            }
        )
    return ExecutionResult(
        provider=result.provider,
        status=result.status,
        artifacts=artifacts,
        events=result.events,
        verification=verification,
        error=result.error,
    )


def _runtime_execution_exception_result(plan: Plan, run_id: str, exc: Exception) -> ExecutionResult:
    provider = plan.primary_provider
    error_type = exc.__class__.__name__
    error_message = redact_text(str(exc)) or ""
    error = f"Runtime execution raised {error_type}"
    if error_message:
        error = f"{error}: {error_message}"
    event = {
        "at": utc_now(),
        "type": "execution_exception",
        "provider": provider,
        "reason": "runtime_execution_exception",
        "error_type": error_type,
        "message": error_message,
    }
    attempt = {
        "order": 1,
        "provider": provider,
        "status": "failed",
        "error": error,
        "artifact_count": 0,
        "verification": {
            "confidence": "low",
            "checks": ["runtime execution exception was captured"],
            "failures": [{"type": "runtime_execution_exception", "provider": provider, "error_type": error_type, "message": error_message}],
        },
    }
    verification = {
        "confidence": "low",
        "checks": ["runtime execution exception was captured"],
        "selected_provider": provider,
        "attempts": [attempt],
        "cost_estimate": plan.cost_estimate,
        "failures": [{"type": "runtime_execution_exception", "provider": provider, "error_type": error_type, "message": error_message}],
    }
    artifacts: list[dict] = []
    try:
        artifact_dir = default_state_dir() / "artifacts" / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        meta_path = artifact_dir / "runtime-exception.json"
        meta_path.write_text(
            safe_json_dumps(
                {
                    "run_id": run_id,
                    "provider": provider,
                    "error_type": error_type,
                    "error": error_message,
                }
            ),
            encoding="utf-8",
        )
        exception_artifact = annotate_artifact({"type": "runtime_exception", "path": str(meta_path), "provider": provider, "reason": "runtime_execution_exception"})
        artifacts.append(exception_artifact)
        attempt["artifact_count"] = len(artifacts)
        report_path = artifact_dir / "run-report.json"
        report = {
            "run_id": run_id,
            "plan_sha256": plan_fingerprint(plan),
            "primary_provider": plan.primary_provider,
            "fallback_providers": plan.fallback_providers,
            "final_provider": provider,
            "final_status": "failed",
            "final_error": error,
            "attempts": [attempt],
            "artifact_count": len(artifacts),
            "artifacts": artifacts,
            "cost_estimate": plan.cost_estimate,
            "verification": verification,
        }
        report_path.write_text(safe_json_dumps(report), encoding="utf-8")
        artifacts.append(annotate_artifact({"type": "run_report", "path": str(report_path), "provider": provider, "attempts": 1}))
        verification["run_report"] = str(report_path)
        verification["checks"].append("run-report.json written for runtime exception")
    except Exception as write_exc:
        verification["checks"].append("runtime exception artifact write failed")
        verification["failures"].append(
            {
                "type": "runtime_exception_artifact_write_failed",
                "provider": provider,
                "error_type": write_exc.__class__.__name__,
                "message": redact_text(str(write_exc)) or "",
            }
        )
    return ExecutionResult(
        provider=provider,
        status="failed",
        artifacts=artifacts,
        events=[event],
        verification=verification,
        error=error,
    )


def _durable_artifacts(artifacts: list[dict]) -> list[dict]:
    return [artifact for artifact in artifacts if artifact.get("type") in DURABLE_ARTIFACT_TYPES]


def _block_untrusted_resume_evidence(run: RunState, store: RunStore) -> RunState | None:
    if run.status not in {"planned", "approved", "blocked", "failed", "executing"}:
        return None
    from .verifier import verify_run_payload

    verifier_report = verify_run_payload(run.to_dict())
    plan_integrity = verifier_report.get("plan_integrity", {})
    plan_integrity_status = plan_integrity.get("status")
    approval_integrity = verifier_report.get("approval_integrity", {})
    approval_integrity_status = approval_integrity.get("status")
    non_resumable_safety = _non_resumable_safety_stop(verifier_report)
    status_failure = _first_verifier_failure(verifier_report, UNTRUSTED_RUN_REPORT_STATUS_FAILURE_TYPES)
    evidence_failure = _first_verifier_failure(verifier_report, UNTRUSTED_RUN_REPORT_EVIDENCE_FAILURE_TYPES)
    provider_constraint_failure = _first_verifier_failure(verifier_report, UNTRUSTED_PROVIDER_CONSTRAINT_FAILURE_TYPES)
    if status_failure and _approved_retry_transition(run, verifier_report):
        status_failure = None
    if evidence_failure and _evidence_failure_allows_state_transition(run, verifier_report, evidence_failure):
        evidence_failure = None
    if (
        plan_integrity_status not in UNTRUSTED_RUN_REPORT_PLAN_INTEGRITY
        and not status_failure
        and not evidence_failure
        and not provider_constraint_failure
        and approval_integrity_status not in UNTRUSTED_APPROVAL_INTEGRITY
        and not non_resumable_safety
    ):
        return None
    if status_failure:
        reason = "run_report_status_integrity"
        issue_status = "status_mismatch"
        checks = [
            "resume stopped because run-report final_status does not match the current run status",
            f"run-report final_status={_run_report_final_status(verifier_report)}",
        ]
    elif plan_integrity_status in UNTRUSTED_RUN_REPORT_PLAN_INTEGRITY:
        reason = "run_report_plan_integrity"
        issue_status = str(plan_integrity_status)
        checks = [
            "resume stopped because run-report evidence no longer matches the current plan",
            f"run-report plan_integrity={plan_integrity_status}",
        ]
    elif provider_constraint_failure:
        reason = "provider_constraints"
        issue_status = str(provider_constraint_failure.get("type"))
        checks = [
            "resume stopped because provider sequence violates task constraints",
            str(provider_constraint_failure.get("message", provider_constraint_failure.get("type"))),
        ]
    elif evidence_failure:
        reason = "run_report_evidence_integrity"
        issue_status = str(evidence_failure.get("type"))
        checks = [
            "resume stopped because run-report or artifact evidence is missing or inconsistent",
            str(evidence_failure.get("message", evidence_failure.get("type"))),
        ]
    elif non_resumable_safety:
        reason = "non_resumable_safety_stop"
        issue_status = str(non_resumable_safety.get("reason") or "target_scope_safety_stop")
        checks = [
            "resume stopped because this blocked run is a target-scope or DNS safety stop",
            "create a new run or replan for the intended target scope before provider execution",
        ]
    else:
        reason = "approval_integrity"
        issue_status = str(approval_integrity_status)
        checks = [
            "resume stopped because approval evidence no longer matches the current plan",
            f"approval_integrity={approval_integrity_status}",
        ]
    run.events.append(
        {
            "at": utc_now(),
            "type": "resume_blocked",
            "reason": reason,
            "evidence_integrity_status": issue_status,
            "run_report_integrity_status": issue_status if reason.startswith("run_report") else None,
            "plan_integrity_status": plan_integrity_status,
            "approval_integrity_status": approval_integrity_status,
            "approval_id": approval_integrity.get("approval_id"),
            "non_resumable_reason": non_resumable_safety.get("reason") if non_resumable_safety else None,
            "run_status": run.status,
            "run_report_final_status": _run_report_final_status(verifier_report),
            "expected_sha256": plan_integrity.get("expected_sha256"),
            "actual_sha256": plan_integrity.get("actual_sha256"),
        }
    )
    verification = dict(run.verification or {})
    verification["confidence"] = "high"
    verification["checks"] = _append_unique_checks(verification.get("checks", []), checks)
    verification["plan_integrity"] = plan_integrity
    if status_failure or evidence_failure:
        failure = status_failure or evidence_failure or {}
        verification["run_report_integrity"] = {
            "status": "mismatch" if status_failure else "untrusted",
            "failure_type": failure.get("type"),
            "run_status": run.status,
            "run_report_final_status": _run_report_final_status(verifier_report),
        }
    if evidence_failure:
        verification["failures"] = _append_unique_failures(verification.get("failures", []), [evidence_failure])
    if approval_integrity_status in UNTRUSTED_APPROVAL_INTEGRITY:
        verification["approval_integrity"] = approval_integrity
    if provider_constraint_failure:
        verification["failures"] = _append_unique_failures(verification.get("failures", []), [provider_constraint_failure])
    if non_resumable_safety:
        verification["policy_guard"] = verifier_report.get("policy_guard", {})
    run.verification = verification
    store.save(run)
    return run


def _first_verifier_failure(verifier_report: dict, failure_types: set[str]) -> dict | None:
    for failure in verifier_report.get("failures", []):
        if failure.get("type") in failure_types:
            return failure
    return None


def _run_report_final_status(verifier_report: dict) -> str | None:
    run_report = verifier_report.get("run_report") or {}
    return run_report.get("final_status")


def _approved_retry_transition(run: RunState, verifier_report: dict) -> bool:
    write_retry_guard = verifier_report.get("write_retry_guard") or {}
    return bool(run.status == "approved" and write_retry_guard.get("retry_approval_after_last_attempt"))


def _evidence_failure_allows_state_transition(run: RunState, verifier_report: dict, failure: dict) -> bool:
    if failure.get("type") != "missing_run_report":
        return False
    write_retry_guard = verifier_report.get("write_retry_guard") or {}
    if write_retry_guard.get("fresh_retry_approval_required"):
        return True
    return any(event.get("type") == "stale_execution_recovered" for event in run.events)


def _non_resumable_safety_stop(verifier_report: dict) -> dict | None:
    policy_guard = verifier_report.get("policy_guard") or {}
    if not policy_guard.get("non_resumable_safety_stop"):
        return None
    reason = policy_guard.get("non_resumable_reason") or "target_scope_safety_stop"
    return {"reason": reason}


def _block_duplicate_external_write_retry(run: RunState, plan: Plan, store: RunStore) -> RunState | None:
    if not _is_external_write_plan(plan):
        return None
    fingerprint = action_fingerprint_from_plan(plan)
    last_attempt_at = _last_external_write_attempt_at(run, fingerprint)
    if not last_attempt_at:
        return None
    if _approved_retry_after(run, last_attempt_at):
        return None
    if _pending_approval_exists(run):
        run.status = "awaiting_approval"
        run.events.append({"at": utc_now(), "type": "external_write_retry_blocked", "reason": "pending_retry_approval", "action_fingerprint": fingerprint})
    else:
        run.status = "awaiting_approval"
        run.approvals.append(
            approval_request_from_plan(
                plan,
                required_before="provider_retry",
                reason="A previous approved external-write attempt already started; fresh approval is required before retry.",
            )
        )
        run.events.append({"at": utc_now(), "type": "external_write_retry_blocked", "reason": "fresh_approval_required", "action_fingerprint": fingerprint})
    run.verification = {
        "confidence": "high",
        "checks": [
            "external write retry was stopped",
            "fresh approval is required before another publish/send/submit attempt",
        ],
    }
    store.save(run)
    return run


def _block_expired_approval(run: RunState, plan: Plan, store: RunStore) -> RunState | None:
    if run.status != "approved" or not _plan_requires_approval(plan):
        return None
    approval = _latest_decided_approval(run, "approved")
    if not approval:
        return None
    ttl_seconds = _approval_ttl_seconds()
    decided_at = _parse_utc_datetime(approval.get("decided_at"))
    if decided_at is None:
        # Fail closed: an approval whose decision timestamp is missing or
        # unparseable cannot be proven fresh, so require a new approval.
        age_seconds = None
    else:
        age_seconds = (datetime.now(timezone.utc) - decided_at).total_seconds()
        if age_seconds <= ttl_seconds:
            return None
    required_before = approval.get("required_before")
    if required_before not in {"provider_execution", "provider_retry"}:
        required_before = "provider_execution"
    run.status = "awaiting_approval"
    run.approvals.append(
        approval_request_from_plan(
            plan,
            required_before=required_before,
            reason="The previous approval expired before provider execution; fresh approval is required.",
        )
    )
    run.events.append(
        {
            "at": utc_now(),
            "type": "approval_expired",
            "reason": "approval_expired",
            "approval_id": approval.get("approval_id"),
            "approved_at": approval.get("decided_at"),
            "age_seconds": int(age_seconds) if age_seconds is not None else None,
            "ttl_seconds": ttl_seconds,
            "required_before": required_before,
        }
    )
    verification = dict(run.verification or {})
    verification["confidence"] = "high"
    verification["checks"] = _append_unique_checks(
        verification.get("checks", []),
        [
            "approval expired before provider execution",
            "fresh approval is required because the previous approval expired",
        ],
    )
    verification["approval_expiry"] = {
        "status": "expired",
        "approval_id": approval.get("approval_id"),
        "approved_at": approval.get("decided_at"),
        "ttl_seconds": ttl_seconds,
        "required_before": required_before,
    }
    run.verification = verification
    store.save(run)
    return run


def _external_write_attempt_event(run: RunState, plan: Plan) -> dict | None:
    if not _is_external_write_plan(plan):
        return None
    approval = _latest_decided_approval(run, "approved")
    fingerprint = action_fingerprint_from_plan(plan)
    return {
        "at": utc_now(),
        "type": "external_write_attempt_started",
        "provider": plan.primary_provider,
        "action_fingerprint": fingerprint,
        "approval_id": approval.get("approval_id") if approval else None,
        "approved_by": approval.get("decided_by") if approval else None,
    }


def _is_external_write_plan(plan: Plan) -> bool:
    return bool(plan.task.external_write or infer_risk(plan.task.goal) in {"external_write", "destructive"})


def _last_external_write_attempt_at(run: RunState, fingerprint: str) -> str | None:
    for event in reversed(run.events):
        if event.get("type") == "external_write_attempt_started" and event.get("action_fingerprint") == fingerprint:
            return event.get("at")
    return None


def _approved_retry_after(run: RunState, attempt_at: str) -> bool:
    for item in reversed(run.approvals):
        if item.get("type") != "approval_request" or item.get("status") != "approved":
            continue
        if item.get("required_before") != "provider_retry":
            continue
        decided_at = item.get("decided_at")
        if decided_at and decided_at > attempt_at:
            return True
    return False


def _pending_approval_exists(run: RunState) -> bool:
    return any(item.get("type") == "approval_request" and item.get("status") == "pending" for item in run.approvals)


def _append_unique_checks(existing: list, additions: list[str]) -> list:
    checks = list(existing) if isinstance(existing, list) else []
    for check in additions:
        if check not in checks:
            checks.append(check)
    return checks


def _append_unique_failures(existing: list, additions: list[dict]) -> list[dict]:
    failures = list(existing) if isinstance(existing, list) else []
    seen = {repr(item) for item in failures}
    for failure in additions:
        key = repr(failure)
        if key not in seen:
            failures.append(failure)
            seen.add(key)
    return failures


def _validate_decision_reason(reason: str, action: str) -> str:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(f"{action} reason is required for auditability")
    return reason.strip()


def _validate_decision_actor(actor: str, action: str) -> str:
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError(f"{action} actor is required for auditability")
    return actor.strip()


def _latest_decided_approval(run: RunState, status: str) -> dict | None:
    for item in reversed(run.approvals):
        if item.get("type") == "approval_request" and item.get("status") == status:
            return item
    return None


def _latest_pending_approval(run: RunState) -> dict | None:
    for item in reversed(run.approvals):
        if item.get("type") == "approval_request" and item.get("status") == "pending":
            return item
    return None


def _validate_pending_approval_matches_plan(run: RunState, plan: Plan) -> None:
    approval = _latest_pending_approval(run)
    if not approval:
        raise ValueError(f"Run {run.run_id} has no pending approval request")
    if not approval.get("approval_id"):
        raise ValueError("pending approval request does not match the current plan: approval id is missing")
    if approval.get("required_before") not in {"provider_execution", "provider_retry"}:
        raise ValueError("pending approval request does not match the current plan: unsupported approval stage")
    expected_action_fingerprint = action_fingerprint_from_plan(plan)
    actual_action_fingerprint = approval.get("action_fingerprint")
    if actual_action_fingerprint != expected_action_fingerprint:
        raise ValueError("pending approval request does not match the current plan: action fingerprint mismatch")
    actual_plan_fingerprint = approval.get("plan_sha256")
    if actual_plan_fingerprint != plan_fingerprint(plan):
        raise ValueError("pending approval request does not match the current plan: plan fingerprint mismatch")


def _close_pending_approval(run: RunState, status: str, actor: str, reason: str) -> None:
    for item in reversed(run.approvals):
        if item.get("type") == "approval_request" and item.get("status") == "pending":
            item["status"] = status
            item["decided_at"] = utc_now()
            item["decided_by"] = actor
            item["reason"] = redact_text(reason)
            return
    raise ValueError(f"Run {run.run_id} has no pending approval request")


def _execution_approval_context(run: RunState, plan: Plan) -> dict | None:
    if not _plan_requires_approval(plan):
        return None
    approval = _latest_decided_approval(run, "approved")
    if not approval:
        return None
    return {
        "approval_id": approval.get("approval_id"),
        "status": approval.get("status"),
        "required_before": approval.get("required_before"),
        "action_fingerprint": approval.get("action_fingerprint"),
        "decided_at": approval.get("decided_at"),
        "decided_by": approval.get("decided_by"),
        "plan_sha256": approval.get("plan_sha256"),
    }


def _plan_requires_approval(plan: Plan) -> bool:
    return bool(plan.approval_required or task_approval_required(plan.task))


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


def _execution_lease_seconds(plan: Plan) -> int:
    configured = os.environ.get("SUPER_BROWSER_EXECUTION_LEASE_SECONDS")
    if configured is not None:
        try:
            return max(0, int(configured))
        except ValueError:
            return LONG_RUNNING_EXECUTION_LEASE_SECONDS if _is_long_running_plan(plan) else DEFAULT_EXECUTION_LEASE_SECONDS
    return LONG_RUNNING_EXECUTION_LEASE_SECONDS if _is_long_running_plan(plan) else DEFAULT_EXECUTION_LEASE_SECONDS


def _is_long_running_plan(plan: Plan) -> bool:
    return bool(plan.task.long_running or long_running_for_goal(plan.task.goal))


def _run_from_payload(payload: dict) -> RunState:
    return RunState(
        run_id=payload["run_id"],
        status=payload["status"],
        plan=payload["plan"],
        created_at=payload["created_at"],
        updated_at=payload["updated_at"],
        execution_lease=payload.get("execution_lease", {}),
        artifacts=payload.get("artifacts", []),
        events=payload.get("events", []),
        approvals=payload.get("approvals", []),
        verification=payload.get("verification", {}),
    )


def _plan_from_run(run: RunState) -> Plan:
    from .models import PlanStep, TaskSpec

    task = TaskSpec(**run.plan["task"])
    steps = [PlanStep(**step) for step in run.plan.get("steps", [])]
    return Plan(
        task=task,
        mode=run.plan.get("mode", "direct"),
        primary_provider=run.plan["primary_provider"],
        fallback_providers=run.plan.get("fallback_providers", []),
        steps=steps,
        missing_env=run.plan.get("missing_env", []),
        approval_required=run.plan.get("approval_required", False),
        rationale=run.plan.get("rationale", []),
        council_report=run.plan.get("council_report", {}),
        cost_estimate=run.plan.get("cost_estimate", {}),
    )
