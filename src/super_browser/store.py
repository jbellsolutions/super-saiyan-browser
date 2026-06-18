from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import RUN_STATUS_VALUES, RunState, utc_now
from .redaction import redact


def default_state_dir() -> Path:
    return Path(os.environ.get("SUPER_BROWSER_STATE_DIR", ".super-browser"))


class RunStore:
    def __init__(self, path: str | Path | None = None, create: bool = True):
        if path:
            self.path = Path(path)
            if create:
                self.path.parent.mkdir(parents=True, exist_ok=True)
        else:
            state_dir = default_state_dir()
            if create:
                state_dir.mkdir(parents=True, exist_ok=True)
            self.path = state_dir / "runs.sqlite"
        if create:
            self._init()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def save(self, run: RunState) -> None:
        run.updated_at = utc_now()
        payload = json.dumps(redact(run.to_dict()), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (run_id, status, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (run.run_id, run.status, payload, run.created_at, run.updated_at),
            )

    def claim_execution(self, run_id: str, expected_status: str, events: list[dict[str, Any]], lease_seconds: int) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status, payload FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if not row:
                conn.commit()
                return None
            current_status, raw_payload = row
            payload = _decode_run_payload(raw_payload, run_id=run_id, status=current_status)
            if _payload_is_corrupt(payload):
                conn.commit()
                return None
            if current_status != expected_status or payload.get("status") != expected_status:
                conn.commit()
                return None
            now = utc_now()
            payload["status"] = "executing"
            payload["updated_at"] = now
            payload["execution_lease"] = {
                "claimed_at": now,
                "lease_expires_at": _iso_after(now, lease_seconds),
                "lease_seconds": lease_seconds,
            }
            payload.setdefault("events", []).extend(redact(events))
            conn.execute(
                "UPDATE runs SET status = ?, payload = ?, updated_at = ? WHERE run_id = ?",
                ("executing", json.dumps(redact(payload), sort_keys=True), now, run_id),
            )
            conn.commit()
            return payload
        finally:
            conn.close()

    def recover_stale_execution(self, run_id: str, lease_seconds: int, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status, payload, updated_at FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if not row:
                conn.commit()
                return None
            current_status, raw_payload, row_updated_at = row
            payload = _decode_run_payload(raw_payload, run_id=run_id, status=current_status, updated_at=row_updated_at)
            if _payload_is_corrupt(payload):
                conn.commit()
                return None
            if current_status != "executing" or payload.get("status") != "executing":
                conn.commit()
                return None
            if not _execution_lease_expired(payload, row_updated_at, lease_seconds):
                conn.commit()
                return None
            now = utc_now()
            payload["status"] = "failed"
            payload["updated_at"] = now
            payload["execution_lease"] = {}
            payload.setdefault("events", []).extend(redact(events))
            payload["verification"] = {
                "confidence": "medium",
                "checks": [
                    "stale executing run was recovered",
                    "previous provider attempt expired before completion",
                ],
            }
            conn.execute(
                "UPDATE runs SET status = ?, payload = ?, updated_at = ? WHERE run_id = ?",
                ("failed", json.dumps(redact(payload), sort_keys=True), now, run_id),
            )
            conn.commit()
            return payload
        finally:
            conn.close()

    def get(self, run_id: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT run_id, status, payload, created_at, updated_at FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return None
            raise
        if not row:
            return None
        row_run_id, status, raw_payload, created_at, updated_at = row
        return _decode_run_payload(raw_payload, run_id=row_run_id, status=status, created_at=created_at, updated_at=updated_at)

    def list(self, status: str | None = None, limit: int | None = None, include_details: bool = True) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        query = "SELECT payload FROM runs"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(0, limit))
        try:
            with self._connect() as conn:
                rows = conn.execute(query.replace("SELECT payload", "SELECT run_id, status, payload, created_at, updated_at"), params).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return []
            raise
        runs = [
            _decode_run_payload(raw_payload, run_id=run_id, status=status, created_at=created_at, updated_at=updated_at)
            for run_id, status, raw_payload, created_at, updated_at in rows
        ]
        if include_details:
            return runs
        return [summarize_run(run) for run in runs]


def summarize_run(run: dict[str, Any]) -> dict[str, Any]:
    plan = run.get("plan") or {}
    task = plan.get("task") or {}
    verification = run.get("verification") or {}
    approvals = run.get("approvals") or []
    summary = {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "goal": task.get("goal"),
        "url": task.get("url"),
        "primary_provider": plan.get("primary_provider"),
        "fallback_providers": plan.get("fallback_providers", []),
        "approval_required": bool(plan.get("approval_required")),
        "pending_approval": any(item.get("type") == "approval_request" and item.get("status") == "pending" for item in approvals),
        "event_count": len(run.get("events") or []),
        "artifact_count": len(run.get("artifacts") or []),
        "confidence": verification.get("confidence"),
    }
    if run.get("store_error"):
        summary["store_error"] = run["store_error"]
    return summary


def _decode_run_payload(
    raw_payload: Any,
    *,
    run_id: str | None = None,
    status: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    try:
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            raise ValueError("stored run payload is not a JSON object")
        return payload
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return _corrupt_run_payload(
            run_id=run_id,
            status=status,
            created_at=created_at,
            updated_at=updated_at,
            error_type=exc.__class__.__name__,
        )


def _payload_is_corrupt(payload: dict[str, Any]) -> bool:
    return (payload.get("store_error") or {}).get("type") == "store_payload_corrupt"


def _corrupt_run_payload(
    *,
    run_id: str | None,
    status: str | None,
    created_at: str | None,
    updated_at: str | None,
    error_type: str,
) -> dict[str, Any]:
    now = utc_now()
    safe_status = status if status in RUN_STATUS_VALUES else "unknown"
    failure = {
        "type": "store_payload_corrupt",
        "message": "Stored run payload could not be decoded; provider execution is blocked until the run is replanned.",
        "stored_status": safe_status,
        "error_type": error_type,
    }
    return {
        "run_id": str(run_id or "run_corrupt_payload"),
        "status": "failed",
        "plan": {},
        "created_at": created_at or now,
        "updated_at": updated_at or now,
        "execution_lease": {},
        "artifacts": [],
        "events": [
            {
                "at": updated_at or now,
                "type": "store_payload_corrupt",
                "reason": "stored_run_payload_decode_failed",
                "stored_status": safe_status,
            }
        ],
        "approvals": [],
        "verification": {
            "confidence": "low",
            "checks": ["stored run payload could not be decoded", "provider execution is blocked until the run is replanned"],
            "failures": [failure],
        },
        "store_error": failure,
    }


def _execution_lease_expired(payload: dict[str, Any], row_updated_at: str, lease_seconds: int) -> bool:
    lease = payload.get("execution_lease") or {}
    lease_expires_at = lease.get("lease_expires_at")
    if not lease_expires_at:
        lease_expires_at = _iso_after(payload.get("updated_at") or row_updated_at, lease_seconds)
    return _parse_utc(lease_expires_at) <= datetime.now(timezone.utc)


def _iso_after(base_iso: str, seconds: int) -> str:
    return (_parse_utc(base_iso) + timedelta(seconds=max(0, seconds))).isoformat()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
