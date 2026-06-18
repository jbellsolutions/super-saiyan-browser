from __future__ import annotations

from typing import Any

from ...brightdata.datasets import scrape_dataset_url


def run_facebook_group(url: str) -> dict[str, Any]:
    return scrape_dataset_url(url, tool="facebook_groups")


def run_facebook_page(url: str) -> dict[str, Any]:
    return scrape_dataset_url(url, tool="facebook_posts")
