from __future__ import annotations

from typing import Any

from .providers import PROVIDERS, provider_readiness


DEFAULT_REQUIRED_PROVIDERS = list(PROVIDERS.keys())


def production_readiness(required_providers: list[str] | None = None) -> dict[str, Any]:
    required = required_providers or DEFAULT_REQUIRED_PROVIDERS
    rows = {row["name"]: row for row in provider_readiness()}
    provider_reports: list[dict[str, Any]] = []
    blocked_providers: list[str] = []
    ready_providers: list[str] = []
    uncertified_providers: list[str] = []
    missing_env: list[str] = []
    blockers: list[dict[str, Any]] = []
    next_actions: list[str] = []

    for provider_name in required:
        row = rows.get(provider_name)
        if not row:
            blocked_providers.append(provider_name)
            blockers.append({"provider": provider_name, "blockers": ["unknown provider"]})
            continue

        provider_blockers = list(row.get("production_blockers") or [])
        provider_missing_required = list(row.get("missing_required_env") or [])
        provider_missing_optional = list(row.get("missing_optional_env") or [])
        provider_uncertified = list(row.get("uncertified_workflow_classes") or [])
        production_ready = bool(row.get("production_ready"))
        broader_certification_missing = bool(row.get("requires_live_test_before_broader_production") or provider_uncertified)
        provider_fully_ready = bool(production_ready and not broader_certification_missing and not provider_missing_required)

        missing_env.extend(provider_missing_required)
        if provider_name == "decodo-http":
            missing_env.extend(provider_missing_optional)

        if provider_fully_ready:
            ready_providers.append(provider_name)
        else:
            blocked_providers.append(provider_name)
            if broader_certification_missing and not provider_blockers:
                provider_blockers.append(f"missing fresh live-test evidence for workflow classes: {', '.join(provider_uncertified)}")
            blockers.append(
                {
                    "provider": provider_name,
                    "display_name": row.get("display_name"),
                    "readiness_status": row.get("readiness_status"),
                    "production_ready_scope": row.get("production_ready_scope"),
                    "missing_required_env": provider_missing_required,
                    "missing_optional_env": provider_missing_optional,
                    "uncertified_workflow_classes": provider_uncertified,
                    "blockers": provider_blockers or [row.get("production_gate") or "provider is not production-ready"],
                }
            )

        if provider_uncertified and not provider_missing_required:
            uncertified_providers.append(provider_name)

        action = _next_action(provider_name, row, provider_missing_required, provider_uncertified)
        if action and action not in next_actions:
            next_actions.append(action)

    production_ready = not blocked_providers
    return {
        "status": "ready" if production_ready else "blocked",
        "production_ready": production_ready,
        "required_providers": required,
        "ready_providers": ready_providers,
        "blocked_providers": blocked_providers,
        "uncertified_providers": uncertified_providers,
        "missing_env": sorted(set(missing_env)),
        "blockers": blockers,
        "next_actions": next_actions,
        "providers": [rows[name] for name in required if name in rows],
        "summary": {
            "required_provider_count": len(required),
            "ready_provider_count": len(ready_providers),
            "blocked_provider_count": len(blocked_providers),
            "uncertified_provider_count": len(uncertified_providers),
        },
    }


def _next_action(provider_name: str, row: dict[str, Any], missing_required: list[str], uncertified: list[str]) -> str | None:
    if missing_required:
        return f"Configure {provider_name} env vars: {', '.join(missing_required)}."
    if row.get("readiness_status") in {"package_missing", "runtime_missing"}:
        return str(row.get("next_action") or row.get("production_gate") or "Install missing provider runtime prerequisites.")
    if uncertified:
        classes = ", ".join(uncertified)
        return f"Run live-test evidence for {provider_name}: super-browser live-test --provider {provider_name} --workflow-class <{classes}>."
    gate = row.get("production_gate")
    if gate and not row.get("production_ready"):
        return str(gate)
    return None
