from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from ...brightdata.unlocker import unlock_url
from ...brightdata.client import BrightDataError
from ...proxy import resolve_proxy_url


def fetch_url(
    url: str,
    *,
    prefer_brightdata: bool = True,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    if prefer_brightdata and os.environ.get("BRIGHTDATA_API_KEY") and os.environ.get("BRIGHTDATA_UNLOCKER_ZONE"):
        try:
            payload = unlock_url(url, timeout_seconds=timeout_seconds)
            return {"source": "brightdata-unlocker", "url": url, "content": payload.get("content"), "metadata": payload}
        except BrightDataError:
            pass
    proxy_url = resolve_proxy_url(None)
    headers = {"User-Agent": "super-browser/1.0"}
    request = Request(url, headers=headers)
    opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url})) if proxy_url else build_opener()
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    return {"source": "raw-http", "url": url, "content": body}


def fetch_json(url: str, **kwargs: Any) -> Any:
    payload = fetch_url(url, **kwargs)
    content = payload.get("content") or ""
    return json.loads(content)
