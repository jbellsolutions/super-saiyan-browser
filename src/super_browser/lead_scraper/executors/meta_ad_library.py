from __future__ import annotations

from typing import Any

from ...brightdata.unlocker import unlock_url
from ...brightdata.client import BrightDataError
from .http_util import fetch_url


def run_meta_ad_library(url: str, *, use_browser_fallback: bool = False) -> dict[str, Any]:
    try:
        payload = unlock_url(url)
        return {"source": "brightdata-unlocker", "url": url, "content": payload.get("content"), "metadata": payload}
    except BrightDataError as exc:
        if not use_browser_fallback:
            raise
        from ...brightdata.browser import scrape_with_browser

        browser_payload = scrape_with_browser(url)
        return {"source": "brightdata-browser", "url": url, "payload": browser_payload}
    except Exception:
        return fetch_url(url, prefer_brightdata=False)
