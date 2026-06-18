from __future__ import annotations

from typing import Any

from .http_util import fetch_url


def fetch_publicwww_page(url: str) -> dict[str, Any]:
    return fetch_url(url)
