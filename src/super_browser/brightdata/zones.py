from __future__ import annotations

import os
from dataclasses import dataclass

_discovery_attempted = False


@dataclass(frozen=True)
class BrightDataConfig:
    api_base: str
    api_key: str | None
    unlocker_zone: str | None
    serp_zone: str | None
    browser_zone: str | None
    browser_username: str | None
    browser_password: str | None
    customer_id: str | None
    serp_uses_unlocker_fallback: bool


LANE_ENV: dict[str, list[str]] = {
    "brightdata-unlocker": ["BRIGHTDATA_API_KEY", "BRIGHTDATA_UNLOCKER_ZONE"],
    "brightdata-serp": ["BRIGHTDATA_API_KEY"],
    "brightdata-browser": ["BRIGHTDATA_BROWSER_USERNAME", "BRIGHTDATA_BROWSER_PASSWORD"],
    "brightdata-dataset": ["BRIGHTDATA_API_KEY"],
}


def brightdata_config() -> BrightDataConfig:
    _ensure_zone_discovery()
    browser_username = os.environ.get("BRIGHTDATA_BROWSER_USERNAME")
    browser_password = os.environ.get("BRIGHTDATA_BROWSER_PASSWORD")
    browser_zone = os.environ.get("BRIGHTDATA_BROWSER_ZONE")
    customer_id = os.environ.get("BRIGHTDATA_CUSTOMER_ID")
    if not browser_username and browser_zone and customer_id and browser_password:
        browser_username = f"brd-customer-{customer_id}-zone-{browser_zone}"
    unlocker_zone = os.environ.get("BRIGHTDATA_UNLOCKER_ZONE")
    serp_zone = os.environ.get("BRIGHTDATA_SERP_ZONE")
    serp_fallback = False
    if not serp_zone and unlocker_zone:
        serp_zone = unlocker_zone
        serp_fallback = True
    return BrightDataConfig(
        api_base=os.environ.get("BRIGHTDATA_API_BASE", "https://api.brightdata.com").rstrip("/"),
        api_key=os.environ.get("BRIGHTDATA_API_KEY"),
        unlocker_zone=unlocker_zone,
        serp_zone=serp_zone,
        browser_zone=browser_zone,
        browser_username=browser_username,
        browser_password=browser_password,
        customer_id=customer_id,
        serp_uses_unlocker_fallback=serp_fallback,
    )


def missing_env_for_lane(lane: str) -> list[str]:
    _ensure_zone_discovery()
    if lane == "brightdata-serp":
        missing: list[str] = []
        if not os.environ.get("BRIGHTDATA_API_KEY"):
            missing.append("BRIGHTDATA_API_KEY")
        if not _effective_serp_zone():
            missing.append("BRIGHTDATA_SERP_ZONE")
        return missing
    required = LANE_ENV.get(lane, [])
    missing = []
    for name in required:
        if name.startswith("BRIGHTDATA_BROWSER_"):
            if lane == "brightdata-browser" and not _browser_credentials_ready():
                if name not in missing:
                    missing.append(name)
            continue
        if not os.environ.get(name):
            missing.append(name)
    if lane == "brightdata-browser" and not _browser_credentials_ready():
        return [name for name in required if not _browser_env_present(name)]
    return missing


def _effective_serp_zone() -> str | None:
    return os.environ.get("BRIGHTDATA_SERP_ZONE") or os.environ.get("BRIGHTDATA_UNLOCKER_ZONE")


def _browser_credentials_ready() -> bool:
    cfg = brightdata_config()
    return bool(cfg.browser_username and cfg.browser_password)


def _browser_env_present(name: str) -> bool:
    if name == "BRIGHTDATA_BROWSER_USERNAME":
        return bool(brightdata_config().browser_username)
    if name == "BRIGHTDATA_BROWSER_PASSWORD":
        return bool(brightdata_config().browser_password)
    return bool(os.environ.get(name))


def browser_cdp_url() -> str | None:
    cfg = brightdata_config()
    if not cfg.browser_username or not cfg.browser_password:
        return None
    auth = f"{cfg.browser_username}:{cfg.browser_password}"
    return f"wss://{auth}@brd.superproxy.io:9222"


def ensure_brightdata_zones() -> None:
    """Resolve API key and zone names from env or Bright Data account discovery."""
    _ensure_zone_discovery()


def _ensure_zone_discovery() -> None:
    global _discovery_attempted
    if _discovery_attempted:
        return
    _discovery_attempted = True
    needs_discovery = not os.environ.get("BRIGHTDATA_API_KEY") or (
        not os.environ.get("BRIGHTDATA_UNLOCKER_ZONE")
        and not os.environ.get("BRIGHTDATA_SERP_ZONE")
        and not os.environ.get("BRIGHTDATA_BROWSER_ZONE")
    )
    if not needs_discovery:
        return
    from .zone_discovery import discover_and_apply

    discover_and_apply()
