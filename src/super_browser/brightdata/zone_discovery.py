from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .client import BrightDataClient, BrightDataError

ZONE_TYPE_TO_LANE = {
    "unblocker": "unlocker",
    "serp": "serp",
    "serp_api": "serp",
    "browser_api": "browser",
}

LANE_ENV_NAMES = {
    "unlocker": "BRIGHTDATA_UNLOCKER_ZONE",
    "serp": "BRIGHTDATA_SERP_ZONE",
    "browser": "BRIGHTDATA_BROWSER_ZONE",
}


@dataclass(frozen=True)
class DiscoveredZones:
    api_key_source: str | None
    unlocker_zone: str | None
    serp_zone: str | None
    browser_zone: str | None
    browser_password: str | None
    serp_uses_unlocker_fallback: bool
    notes: tuple[str, ...]


def resolve_api_key() -> tuple[str | None, str | None]:
    env_key = os.environ.get("BRIGHTDATA_API_KEY", "").strip()
    if env_key:
        return env_key, "env"
    for path in _mcp_config_paths():
        token = _read_mcp_token(path)
        if token:
            return token, f"mcp:{path}"
    return None, None


def discover_zones(*, api_key: str | None = None, api_base: str | None = None) -> DiscoveredZones:
    key, key_source = (api_key, "caller") if api_key else resolve_api_key()
    notes: list[str] = []
    if not key:
        return DiscoveredZones(None, None, None, None, None, False, ("No Bright Data API key in env or Cursor MCP config.",))

    client = BrightDataClient(api_key=key, api_base=api_base or os.environ.get("BRIGHTDATA_API_BASE"))
    active = _fetch_active_zones(client)
    by_lane: dict[str, str] = {}
    for entry in active:
        zone_name = str(entry.get("name") or "").strip()
        zone_type = str(entry.get("type") or "").strip().lower()
        lane = ZONE_TYPE_TO_LANE.get(zone_type)
        if zone_name and lane and lane not in by_lane:
            by_lane[lane] = zone_name

    unlocker_zone = by_lane.get("unlocker")
    serp_zone = by_lane.get("serp")
    browser_zone = by_lane.get("browser")
    serp_fallback = False
    if not serp_zone and unlocker_zone:
        serp_zone = unlocker_zone
        serp_fallback = True
        notes.append("No dedicated SERP zone; using Web Unlocker zone for Google/Bing search requests.")

    browser_password: str | None = None
    if browser_zone:
        browser_password = _fetch_zone_password(client, browser_zone)
        if browser_password:
            notes.append("Browser zone password fetched from Bright Data API.")
        else:
            notes.append("Browser zone found but password could not be fetched.")
        if not os.environ.get("BRIGHTDATA_BROWSER_USERNAME") and not os.environ.get("BRIGHTDATA_CUSTOMER_ID"):
            notes.append(
                "Browser lane still needs BRIGHTDATA_BROWSER_USERNAME or BRIGHTDATA_CUSTOMER_ID "
                "(Account settings in Bright Data control panel)."
            )

    return DiscoveredZones(
        api_key_source=key_source,
        unlocker_zone=unlocker_zone,
        serp_zone=serp_zone,
        browser_zone=browser_zone,
        browser_password=browser_password,
        serp_uses_unlocker_fallback=serp_fallback,
        notes=tuple(notes),
    )


def discover_and_apply(*, force: bool = False) -> DiscoveredZones:
    discovered = discover_zones()
    key, _ = resolve_api_key()
    if key and (force or not os.environ.get("BRIGHTDATA_API_KEY")):
        os.environ["BRIGHTDATA_API_KEY"] = key
    if discovered.unlocker_zone and (force or not os.environ.get("BRIGHTDATA_UNLOCKER_ZONE")):
        os.environ["BRIGHTDATA_UNLOCKER_ZONE"] = discovered.unlocker_zone
    if discovered.serp_zone and not discovered.serp_uses_unlocker_fallback:
        if force or not os.environ.get("BRIGHTDATA_SERP_ZONE"):
            os.environ["BRIGHTDATA_SERP_ZONE"] = discovered.serp_zone
    if discovered.browser_zone and (force or not os.environ.get("BRIGHTDATA_BROWSER_ZONE")):
        os.environ["BRIGHTDATA_BROWSER_ZONE"] = discovered.browser_zone
    if discovered.browser_password and (force or not os.environ.get("BRIGHTDATA_BROWSER_PASSWORD")):
        os.environ["BRIGHTDATA_BROWSER_PASSWORD"] = discovered.browser_password
    return discovered


def discovery_report(discovered: DiscoveredZones | None = None) -> dict[str, Any]:
    found = discovered or discover_zones()
    return {
        "api_key_configured": bool(resolve_api_key()[0]),
        "api_key_source": found.api_key_source,
        "unlocker_zone": found.unlocker_zone,
        "serp_zone": found.serp_zone,
        "serp_uses_unlocker_fallback": found.serp_uses_unlocker_fallback,
        "browser_zone": found.browser_zone,
        "browser_password_configured": bool(found.browser_password or os.environ.get("BRIGHTDATA_BROWSER_PASSWORD")),
        "browser_username_configured": bool(os.environ.get("BRIGHTDATA_BROWSER_USERNAME")),
        "customer_id_configured": bool(os.environ.get("BRIGHTDATA_CUSTOMER_ID")),
        "notes": list(found.notes),
        "what_are_zones": (
            "Bright Data zones are named product instances in your account (e.g. mcp_unlocker, mcp_browser). "
            "Super Saiyan Browser discovers them automatically; you only need an API key or the Bright Data MCP in Cursor."
        ),
    }


def write_discovered_env(path: Path, *, force: bool = False) -> dict[str, Any]:
    discovered = discover_and_apply(force=force)
    from ..env_file import merge_env_file

    updates: dict[str, str] = {}
    key, _ = resolve_api_key()
    if key and (force or not _env_file_has(path, "BRIGHTDATA_API_KEY")):
        updates["BRIGHTDATA_API_KEY"] = key
    if discovered.unlocker_zone and (force or not _env_file_has(path, "BRIGHTDATA_UNLOCKER_ZONE")):
        updates["BRIGHTDATA_UNLOCKER_ZONE"] = discovered.unlocker_zone
    if discovered.serp_zone and not discovered.serp_uses_unlocker_fallback:
        if force or not _env_file_has(path, "BRIGHTDATA_SERP_ZONE"):
            updates["BRIGHTDATA_SERP_ZONE"] = discovered.serp_zone
    if discovered.browser_zone and (force or not _env_file_has(path, "BRIGHTDATA_BROWSER_ZONE")):
        updates["BRIGHTDATA_BROWSER_ZONE"] = discovered.browser_zone
    if discovered.browser_password and (force or not _env_file_has(path, "BRIGHTDATA_BROWSER_PASSWORD")):
        updates["BRIGHTDATA_BROWSER_PASSWORD"] = discovered.browser_password
    written = merge_env_file(path, updates) if updates else []
    return {
        **discovery_report(discovered),
        "env_path": str(path),
        "written_vars": written,
    }


def _require_key() -> str:
    key, _ = resolve_api_key()
    if not key:
        raise BrightDataError("Bright Data API key is not configured", error_class="auth")
    return key


def _fetch_active_zones(client: BrightDataClient) -> list[dict[str, Any]]:
    payload = client.request_json("GET", "/zone/get_active_zones")
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    return []


def _fetch_zone_password(client: BrightDataClient, zone_name: str) -> str | None:
    payload = client.request_json("GET", "/zone", params={"zone": zone_name})
    if not isinstance(payload, dict):
        return None
    passwords = payload.get("password")
    if isinstance(passwords, list) and passwords:
        return str(passwords[0])
    return None


def _mcp_config_paths() -> list[Path]:
    home = Path.home()
    return [
        home / ".cursor" / "mcp.json",
        home / ".codex" / "mcp.json",
    ]


def _read_mcp_token(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    for name, config in servers.items():
        if not isinstance(config, dict):
            continue
        if "brightdata" not in name.lower():
            continue
        url = str(config.get("url") or "")
        if not url:
            continue
        query = parse_qs(urlparse(url).query)
        token = (query.get("token") or [None])[0]
        if token:
            return str(token).strip()
    return None


def _env_file_has(path: Path, name: str) -> bool:
    if not path.is_file():
        return False
    prefix = f"{name}="
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix) and stripped[len(prefix) :].strip():
            return True
    return False
