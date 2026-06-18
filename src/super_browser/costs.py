from __future__ import annotations

from typing import Any

from .models import TaskSpec
from .providers import PROVIDERS


COST_FLOOR_USD = {"free": 0.0, "low": 0.01, "medium": 0.25, "variable": 0.05, "high": 1.0}


def provider_cost_floor_usd(provider_name: str) -> float:
    provider = PROVIDERS[provider_name]
    return COST_FLOOR_USD.get(provider.cost_band, 1.0)


def estimate_provider_cost(provider_name: str, task: TaskSpec) -> dict[str, Any]:
    provider = PROVIDERS[provider_name]
    base_floor = provider_cost_floor_usd(provider_name)
    multiplier = 1.0
    notes = [f"Base floor from {provider.cost_band} cost band."]
    if task.long_running:
        multiplier += 2.0
        notes.append("Long-running workflow can multiply session/proxy/credit usage.")
    if task.anti_bot_risk and provider.cost_band == "variable":
        multiplier += 1.0
        notes.append("Anti-bot workflow can require retries, recordings, or premium browser sessions.")
    if task.needs_desktop and provider_name == "orgo":
        multiplier += 1.0
        notes.append("Desktop/computer workflow can consume per-machine runtime.")
    if task.requires_auth:
        notes.append("Authenticated workflow may include human reauth and profile/session overhead.")
    estimated_floor = round(base_floor * multiplier, 4)
    return {
        "provider": provider_name,
        "cost_band": provider.cost_band,
        "stability": provider.stability,
        "base_floor_usd": base_floor,
        "multiplier": multiplier,
        "estimated_floor_usd": estimated_floor,
        "max_cost_usd": task.max_cost_usd,
        "budget_status": _budget_status(estimated_floor, task.max_cost_usd),
        "confidence": _cost_confidence(provider.cost_band, provider.stability, task.long_running),
        "notes": notes,
    }


def estimate_sequence_cost(provider_names: list[str], task: TaskSpec) -> dict[str, Any]:
    estimates = [estimate_provider_cost(name, task) for name in provider_names]
    primary = estimates[0] if estimates else None
    fallback_floor = round(sum(item["estimated_floor_usd"] for item in estimates[1:]), 4)
    total_floor = round(sum(item["estimated_floor_usd"] for item in estimates), 4)
    return {
        "primary": primary,
        "fallbacks": estimates[1:],
        "all_providers": estimates,
        "selected_provider_floor_usd": primary["estimated_floor_usd"] if primary else None,
        "fallback_floor_usd": fallback_floor,
        "worst_case_floor_usd": total_floor,
        "max_cost_usd": task.max_cost_usd,
        "budget_status": _budget_status(primary["estimated_floor_usd"], task.max_cost_usd) if primary else "unknown",
        "notes": [
            "Estimates are routing floors, not vendor billing promises.",
            "Worst-case floor assumes every fallback is attempted once.",
        ],
    }


def _budget_status(estimated_floor: float, max_cost_usd: float | None) -> str:
    if max_cost_usd is None:
        return "no_ceiling"
    return "within_ceiling" if estimated_floor <= max_cost_usd else "over_ceiling"


def _cost_confidence(cost_band: str, stability: str, long_running: bool) -> str:
    if long_running or cost_band == "variable" or stability == "evaluating":
        return "low"
    if cost_band == "free":
        return "high"
    return "medium"
