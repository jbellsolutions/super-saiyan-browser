from __future__ import annotations

from typing import Any

from .client import BrightDataClient, BrightDataError
from .zones import brightdata_config, missing_env_for_lane


def unlock_url(
    url: str,
    *,
    data_format: str = "markdown",
    country: str | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    missing = missing_env_for_lane("brightdata-unlocker")
    if missing:
        raise BrightDataError(f"Missing env: {', '.join(missing)}", error_class="auth")
    cfg = brightdata_config()
    client = BrightDataClient(timeout_seconds=timeout_seconds)
    content = client.unlock_request(zone=str(cfg.unlocker_zone), url=url, data_format=data_format, country=country)
    return {
        "url": url,
        "zone": cfg.unlocker_zone,
        "data_format": data_format,
        "content": content,
        "content_length": len(content or ""),
    }
