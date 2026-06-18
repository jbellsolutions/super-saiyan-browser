from __future__ import annotations

from typing import Any

from ...brightdata.datasets import scrape_dataset_url, search_dataset


def run_linkedin_url(url: str, *, tool: str | None = None) -> dict[str, Any]:
    return scrape_dataset_url(url, tool=tool)


def run_linkedin_people_search(dataset_id: str, filter_tree: dict[str, Any], *, size: int = 10) -> dict[str, Any]:
    return search_dataset(dataset_id, filter_tree, size=size)
