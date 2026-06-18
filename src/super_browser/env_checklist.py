from __future__ import annotations

import os
from typing import Any

from .providers import PROVIDERS, provider_readiness
from .redaction import redact
from .provider_signup import PROVIDER_SIGNUP


OPTIONAL_PROVIDER_ENV = {
    "orgo": ["ORGO_API_BASE", "ORGO_MODEL", "ORGO_COMPUTER_ID"],
    "airtop": ["AIRTOP_API_BASE", "AIRTOP_TIMEOUT_MINUTES"],
    "decodo-http": ["DECODO_PROXY"],
    "hyperbrowser": ["HYPERBROWSER_API_BASE"],
    "steel": ["STEEL_CDP_URL"],
    "brightdata-unlocker": ["BRIGHTDATA_API_BASE"],
    "brightdata-serp": ["BRIGHTDATA_API_BASE"],
    "brightdata-dataset": ["BRIGHTDATA_API_BASE"],
    "brightdata-browser": ["BRIGHTDATA_BROWSER_ZONE", "BRIGHTDATA_CUSTOMER_ID", "BRIGHTDATA_API_BASE"],
}

GLOBAL_ENV = [
    "SUPER_BROWSER_STATE_DIR",
    "SUPER_BROWSER_REPO_ROOT",
    "SUPER_BROWSER_APPROVAL_TTL_SECONDS",
    "SUPER_BROWSER_SLACK_EXECUTE",
    "SUPER_BROWSER_ALLOW_INTERNAL_PROVIDER_BASES",
    "SUPER_BROWSER_ALLOW_INSECURE_PROVIDER_BASES",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
]

SECRET_ENV = {
    "AIRTOP_API_KEY",
    "BROWSER_USE_API_KEY",
    "BRIGHTDATA_API_KEY",
    "BRIGHTDATA_BROWSER_PASSWORD",
    "DECODO_PROXY",
    "HYPERBROWSER_API_KEY",
    "ORGO_API_KEY",
    "STEEL_API_KEY",
}

ENV_PURPOSES = {
    "AIRTOP_API_BASE": "Optional Airtop API base override.",
    "AIRTOP_API_KEY": "Airtop API credential.",
    "AIRTOP_TIMEOUT_MINUTES": "Optional Airtop session timeout in minutes.",
    "BROWSER_USE_API_KEY": "Browser Use Cloud API credential.",
    "BRIGHTDATA_API_BASE": "Optional Bright Data API base override.",
    "BRIGHTDATA_API_KEY": "Bright Data API credential for unlocker, SERP, and dataset lanes.",
    "BRIGHTDATA_BROWSER_PASSWORD": "Bright Data Scraping Browser zone password.",
    "BRIGHTDATA_BROWSER_USERNAME": "Bright Data Scraping Browser zone username.",
    "BRIGHTDATA_BROWSER_ZONE": "Bright Data Scraping Browser zone name (optional if username is full value).",
    "BRIGHTDATA_CUSTOMER_ID": "Bright Data customer id for constructing browser username.",
    "BRIGHTDATA_SERP_ZONE": "Optional Bright Data SERP zone (auto-discovered; unlocker zone is used when absent).",
    "BRIGHTDATA_UNLOCKER_ZONE": "Optional Bright Data Web Unlocker zone (auto-discovered from account).",
    "DECODO_PROXY": "Optional Decodo residential proxy URL for raw HTTP routing.",
    "HYPERBROWSER_API_BASE": "Optional Hyperbrowser API base override.",
    "HYPERBROWSER_API_KEY": "Hyperbrowser API credential.",
    "ORGO_API_BASE": "Optional Orgo API base override.",
    "ORGO_API_KEY": "Orgo API credential.",
    "ORGO_COMPUTER_ID": "Optional pinned Orgo computer id; when unset the adapter auto-discovers a running computer or creates one.",
    "ORGO_MODEL": "Optional Orgo model override.",
    "STEEL_API_KEY": "Steel API credential.",
    "STEEL_CDP_URL": "Optional Steel CDP URL override.",
    "SUPER_BROWSER_ALLOW_INSECURE_PROVIDER_BASES": "Explicit opt-in for insecure non-loopback provider transports.",
    "SUPER_BROWSER_ALLOW_INTERNAL_PROVIDER_BASES": "Explicit opt-in for private-network or link-local provider transports.",
    "SUPER_BROWSER_APPROVAL_TTL_SECONDS": "Optional approval freshness window in seconds before a recorded approval must be renewed.",
    "SUPER_BROWSER_REPO_ROOT": "Optional path to a Super Saiyan Browser repo or installed bundle for MCP resources.",
    "SUPER_BROWSER_STATE_DIR": "Optional durable run state directory.",
    "SUPER_BROWSER_SLACK_EXECUTE": "When true, Slack approve also executes the provider (default false).",
    "SLACK_APP_TOKEN": "Slack app-level token (xapp-...) with connections:write for Socket Mode daemon.",
    "SLACK_BOT_TOKEN": "Slack bot OAuth token (xoxb-...) for the optional super-browser agent daemon.",
}


def environment_checklist() -> dict[str, Any]:
    readiness_rows = {row["name"]: row for row in provider_readiness()}
    provider_rows = [_provider_env_row(name, readiness_rows[name]) for name in PROVIDERS if name in readiness_rows]
    required = _dedupe_env(item for row in provider_rows for item in row["required_env"])
    optional = _dedupe_env(item for row in provider_rows for item in row["optional_env"])
    global_env = [_env_item(name, required=False, scope="global") for name in GLOBAL_ENV]
    all_env = _dedupe_env([*required, *optional, *global_env])
    missing_required_env = sorted(item["name"] for item in required if not item["configured"])
    missing_optional_env = sorted(item["name"] for item in optional if not item["configured"])
    return redact(
        {
            "type": "super_browser_env_checklist",
            "status": "missing_required_env" if missing_required_env else "ready",
            "values_included": False,
            "value_policy": "Environment variable values are intentionally omitted. Set them in the process environment or local secret manager; do not paste secret values into chat.",
            "missing_required_env": missing_required_env,
            "missing_optional_env": missing_optional_env,
            "providers": provider_rows,
            "global_env": global_env,
            "all_env": all_env,
            "commands": _commands(),
            "provider_signup": list(PROVIDER_SIGNUP.values()),
            "setup_command": "super-browser setup",
            "notes": [
                "Required provider env vars unlock provider execution; live-test evidence is still required before production claims.",
                "Optional provider env vars configure proxies, persisted contexts, local state, or provider endpoint overrides.",
                "Provider endpoint overrides are preflighted before credentials are sent.",
                "External writes still require approval even when every env var is configured.",
            ],
        }
    )


def _provider_env_row(provider_name: str, readiness: dict[str, Any]) -> dict[str, Any]:
    provider = PROVIDERS[provider_name]
    required_names = list(provider.env_vars)
    optional_names = list(OPTIONAL_PROVIDER_ENV.get(provider_name, []))
    if provider_name == "decodo-http":
        required_names = [name for name in required_names if name != "DECODO_PROXY"]
        optional_names = _append_unique(optional_names, ["DECODO_PROXY"])
    required_env = [_env_item(name, required=True, provider=provider_name) for name in required_names]
    optional_env = [_env_item(name, required=False, provider=provider_name) for name in optional_names]
    supported_classes = list(readiness.get("supported_live_workflow_classes") or [])
    return {
        "name": provider_name,
        "display_name": provider.display_name,
        "stability": provider.stability,
        "required_env": required_env,
        "optional_env": optional_env,
        "missing_required_env": sorted(item["name"] for item in required_env if not item["configured"]),
        "missing_optional_env": sorted(item["name"] for item in optional_env if not item["configured"]),
        "readiness_status": readiness.get("readiness_status"),
        "usable_now": readiness.get("usable_now"),
        "production_ready": readiness.get("production_ready"),
        "production_ready_scope": readiness.get("production_ready_scope"),
        "supported_live_workflow_classes": supported_classes,
        "live_test_commands": [
            f"super-browser live-test --provider {provider_name} --workflow-class {workflow_class}"
            for workflow_class in supported_classes
        ],
        "next_action": readiness.get("next_action"),
    }


def _env_item(name: str, *, required: bool, provider: str | None = None, scope: str = "provider") -> dict[str, Any]:
    signup = PROVIDER_SIGNUP.get(name, {})
    item = {
        "name": name,
        "scope": scope,
        "provider": provider,
        "required": required,
        "configured": bool(os.environ.get(name)),
        "sensitive": name in SECRET_ENV,
        "purpose": ENV_PURPOSES.get(name, "Super Saiyan Browser configuration value."),
        "value_included": False,
    }
    if signup.get("signup_url"):
        item["signup_url"] = signup["signup_url"]
    if signup.get("docs_url"):
        item["docs_url"] = signup["docs_url"]
    return item


def _dedupe_env(items) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        name = item["name"]
        if name in seen:
            continue
        seen.add(name)
        deduped.append(item)
    return deduped


def _append_unique(values: list[str], additions: list[str]) -> list[str]:
    output = list(values)
    for value in additions:
        if value not in output:
            output.append(value)
    return output


def _commands() -> list[str]:
    return [
        "super-browser setup",
        "super-browser env-checklist",
        "super-browser doctor",
        "super-browser production-readiness",
        "super-browser live-test --provider all",
        "super-browser bundle-manifest",
    ]
