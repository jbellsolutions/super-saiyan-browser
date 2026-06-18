from __future__ import annotations

from typing import Any

from .google_dorks import run_google_dorks
from .http_util import fetch_url


def run_directory_serp(queries: list[str]) -> list[dict[str, Any]]:
    return run_google_dorks(queries)


def fetch_directory_page(url: str) -> dict[str, Any]:
    return fetch_url(url)
