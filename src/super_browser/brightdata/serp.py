from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote_plus

from .client import BrightDataClient, BrightDataError
from .zones import brightdata_config, missing_env_for_lane

ENGINE_BASE = {
    "google": "https://www.google.com/search",
    "bing": "https://www.bing.com/search",
    "yandex": "https://yandex.com/search/",
}


def build_search_url(query: str, *, engine: str = "google", geo: str | None = None) -> str:
    engine_key = engine.lower()
    if engine_key not in ENGINE_BASE:
        raise BrightDataError(f"Unsupported SERP engine: {engine}", error_class="fatal")
    encoded = quote_plus(query)
    if engine_key == "google":
        url = f"{ENGINE_BASE[engine_key]}?q={encoded}&brd_json=1"
        if geo:
            url += f"&gl={geo.lower()}"
        return url
    if engine_key == "bing":
        return f"{ENGINE_BASE[engine_key]}?q={encoded}"
    return f"{ENGINE_BASE[engine_key]}?text={encoded}"


def search(
    query: str,
    *,
    engine: str = "google",
    geo: str | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    missing = missing_env_for_lane("brightdata-serp")
    if missing:
        raise BrightDataError(f"Missing env: {', '.join(missing)}", error_class="auth")
    cfg = brightdata_config()
    target_url = build_search_url(query, engine=engine, geo=geo)
    client = BrightDataClient(timeout_seconds=timeout_seconds)
    raw = client.request_text(
        "POST",
        "/request",
        body={
            "zone": cfg.serp_zone,
            "url": target_url,
            "format": "raw",
            "data_format": "markdown",
        },
    )
    parsed = _try_parse_json(raw)
    return {
        "query": query,
        "engine": engine,
        "geo": geo,
        "target_url": target_url,
        "zone": cfg.serp_zone,
        "raw": raw if parsed is None else None,
        "results": parsed if parsed is not None else raw,
    }


def _try_parse_json(raw: str) -> Any | None:
    text = raw.strip()
    if not text:
        return None
    if text[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except Exception:
        return None
