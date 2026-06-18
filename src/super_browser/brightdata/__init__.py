"""Bright Data platform client — unlocker, SERP, datasets, scraping browser."""

from .client import BrightDataClient, BrightDataError, classify_brightdata_error
from .datasets import dataset_tool_for_url, scrape_dataset_url, search_dataset
from .serp import build_search_url, search
from .unlocker import unlock_url
from .zone_discovery import discover_and_apply, discovery_report, write_discovered_env
from .zones import brightdata_config, missing_env_for_lane

__all__ = [
    "BrightDataClient",
    "BrightDataError",
    "classify_brightdata_error",
    "brightdata_config",
    "missing_env_for_lane",
    "discover_and_apply",
    "discovery_report",
    "write_discovered_env",
    "unlock_url",
    "build_search_url",
    "search",
    "dataset_tool_for_url",
    "scrape_dataset_url",
    "search_dataset",
]
