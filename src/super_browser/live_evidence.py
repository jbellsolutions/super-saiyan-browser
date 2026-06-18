from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import utc_now
from .redaction import redact, redact_text, safe_json_dumps
from .store import default_state_dir


DEFAULT_LIVE_EVIDENCE_MAX_AGE_DAYS = 30


def record_live_test_evidence(report: dict[str, Any], requested_provider: str, provider_names: list[str] | set[str]) -> dict[str, Any]:
    if os.environ.get("SUPER_BROWSER_RECORD_LIVE_TEST_EVIDENCE", "1") in {"0", "false", "False"}:
        return {"recorded": False, "reason": "disabled"}
    provider_set = set(provider_names)
    evidence_dir = _evidence_dir()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for result in report.get("results", []):
        provider = result.get("provider")
        if provider not in provider_set:
            continue
        record = _summarize_result(result, requested_provider)
        path = evidence_dir / f"{provider}.json"
        aggregate = _merge_provider_evidence(path, record)
        path.write_text(safe_json_dumps(aggregate), encoding="utf-8")
        written.append(
            {
                "provider": provider,
                "status": record["status"],
                "workflow_class": record["workflow_class"],
                "certified_workflow_classes": aggregate.get("certified_workflow_classes", []),
                "path": str(path),
            }
        )
    return {"recorded": bool(written), "written": written}


def load_live_test_evidence(provider_name: str) -> dict[str, Any] | None:
    path = _evidence_dir(create=False) / f"{provider_name}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    payload = _normalize_loaded_evidence(payload)
    payload["path"] = str(path)
    return redact(payload)


def live_test_evidence_is_fresh(evidence: dict[str, Any]) -> bool:
    recorded_at = evidence.get("recorded_at")
    if not recorded_at:
        return False
    try:
        recorded = datetime.fromisoformat(recorded_at)
    except ValueError:
        return False
    if recorded.tzinfo is None:
        recorded = recorded.replace(tzinfo=timezone.utc)
    max_age_days = _max_age_days()
    return recorded.astimezone(timezone.utc) >= datetime.now(timezone.utc) - timedelta(days=max_age_days)


def _summarize_result(result: dict[str, Any], requested_provider: str) -> dict[str, Any]:
    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    workflow_class = str(result.get("workflow_class") or result.get("scenario") or "general_read")
    status = result.get("status")
    return redact(
        {
            "provider": result.get("provider"),
            "requested_provider": requested_provider,
            "status": status,
            "recorded_at": utc_now(),
            "run_id": result.get("run_id"),
            "scenario": result.get("scenario"),
            "workflow_class": workflow_class,
            "certification_scope": "workflow_class" if status == "passed" else "none",
            "certified_workflow_classes": [workflow_class] if status == "passed" else [],
            "selected_provider": result.get("selected_provider") or verification.get("selected_provider"),
            "confidence": verification.get("confidence"),
            "checks": _checks(result, verification),
            "artifact_count": len(result.get("artifacts") or []),
            "event_count": len(result.get("events") or []),
            "missing_env": result.get("missing_env", []),
            "error": redact_text(result.get("error")),
        }
    )


def _merge_provider_evidence(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    existing = _read_evidence_file(path)
    by_class = _latest_by_workflow_class(existing)
    workflow_class = str(record.get("workflow_class") or "general_read")
    if record.get("status") != "skipped" or workflow_class not in by_class:
        by_class[workflow_class] = record
    aggregate = dict(record)
    aggregate["latest_by_workflow_class"] = _ordered_workflow_records(by_class)
    return _with_workflow_rollup(aggregate)


def _normalize_loaded_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload["latest_by_workflow_class"] = _ordered_workflow_records(_latest_by_workflow_class(payload))
    return _with_workflow_rollup(payload)


def _read_evidence_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _latest_by_workflow_class(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    raw_records = payload.get("latest_by_workflow_class")
    records: dict[str, dict[str, Any]] = {}
    if isinstance(raw_records, dict):
        for key, item in raw_records.items():
            if isinstance(item, dict):
                workflow_class = str(item.get("workflow_class") or key)
                record = dict(item)
                record["workflow_class"] = workflow_class
                records[workflow_class] = record
    if records:
        return records
    workflow_class = payload.get("workflow_class")
    if workflow_class:
        record = dict(payload)
        record.pop("latest_by_workflow_class", None)
        record["workflow_class"] = str(workflow_class)
        return {str(workflow_class): record}
    return {}


def _ordered_workflow_records(records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {key: records[key] for key in sorted(records)}


def _with_workflow_rollup(payload: dict[str, Any]) -> dict[str, Any]:
    rolled = dict(payload)
    by_class = _latest_by_workflow_class(rolled)
    fresh_classes: list[str] = []
    stale_classes: list[str] = []
    annotated_records: dict[str, dict[str, Any]] = {}
    for workflow_class in sorted(by_class):
        record = dict(by_class[workflow_class])
        record["fresh"] = live_test_evidence_is_fresh(record)
        annotated_records[workflow_class] = record
        if record.get("status") == "passed":
            if record["fresh"]:
                fresh_classes.append(workflow_class)
            else:
                stale_classes.append(workflow_class)
    rolled["fresh"] = live_test_evidence_is_fresh(rolled)
    rolled["latest_by_workflow_class"] = annotated_records
    rolled["certified_workflow_classes"] = fresh_classes
    rolled["stale_certified_workflow_classes"] = stale_classes
    if fresh_classes:
        rolled["certification_scope"] = "workflow_class"
    elif stale_classes:
        rolled["certification_scope"] = "stale_workflow_class"
    else:
        rolled["certification_scope"] = "none"
    return rolled


def _checks(result: dict[str, Any], verification: dict[str, Any]) -> list[str]:
    checks = result.get("checks")
    if isinstance(checks, list):
        return [str(item) for item in checks]
    verification_checks = verification.get("checks")
    if isinstance(verification_checks, list):
        return [str(item) for item in verification_checks]
    return []


def _evidence_dir(create: bool = True) -> Path:
    path = default_state_dir() / "live-tests"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _max_age_days() -> int:
    configured = os.environ.get("SUPER_BROWSER_LIVE_EVIDENCE_MAX_AGE_DAYS")
    if configured is None:
        return DEFAULT_LIVE_EVIDENCE_MAX_AGE_DAYS
    try:
        return max(0, int(configured))
    except ValueError:
        return DEFAULT_LIVE_EVIDENCE_MAX_AGE_DAYS
