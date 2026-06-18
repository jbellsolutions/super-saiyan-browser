from __future__ import annotations

from dataclasses import replace
from typing import Any

from .models import RunState, utc_now
from .profiles import ProfileStore
from .runtime import approve_run, create_run


def create_fleet_runs(
    goal: str,
    *,
    fleet_size: int,
    url: str | None = None,
    optimize: str = "balanced",
    execute: bool = True,
    providers_allowed: list[str] | None = None,
    max_cost_usd: float | None = None,
    timeout_seconds: int | None = None,
    profile: str | None = None,
    proxy: str | None = None,
) -> dict[str, Any]:
    if fleet_size < 2:
        raise ValueError("fleet_size must be at least 2")
    if fleet_size > 10:
        raise ValueError("fleet_size must be 10 or fewer")
    runs: list[RunState] = []
    store = ProfileStore()
    for index in range(fleet_size):
        member_goal = f"{goal} [fleet member {index + 1}/{fleet_size}]"
        member_profile = f"{profile}-{index + 1}" if profile else None
        if member_profile and not store.get(member_profile):
            store.create(
                member_profile,
                description=f"Fleet member {index + 1} of {fleet_size}",
                preferred_provider="browser-use",
            )
        run = create_run(
            member_goal,
            url=url,
            optimize=optimize,
            execute=execute,
            providers_allowed=providers_allowed,
            max_cost_usd=max_cost_usd,
            timeout_seconds=timeout_seconds,
            profile=member_profile,
            proxy=proxy,
            fleet_index=index,
        )
        runs.append(run)
    return {
        "fleet_size": fleet_size,
        "profile_base": profile,
        "proxy": proxy,
        "runs": [run.to_dict() for run in runs],
        "status_summary": _fleet_status_summary(runs),
        "created_at": utc_now(),
    }


def approve_fleet(
    run_ids: list[str],
    *,
    approver: str = "user",
    reason: str = "",
    execute: bool = False,
) -> dict[str, Any]:
    approved = []
    for run_id in run_ids:
        approved.append(approve_run(run_id, approver=approver, reason=reason, execute=execute).to_dict())
    return {"approved_count": len(approved), "runs": approved}


def _fleet_status_summary(runs: list[RunState]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for run in runs:
        counts[run.status] = counts.get(run.status, 0) + 1
    return counts
