from __future__ import annotations

from typing import Any

from ...brightdata.serp import search as brightdata_search


def run_google_dorks(queries: list[str], *, engine: str = "google", geo: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for query in queries:
        rows.append({"query": query, "result": brightdata_search(query, engine=engine, geo=geo)})
    return rows
