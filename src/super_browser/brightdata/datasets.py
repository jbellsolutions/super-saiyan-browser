from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .client import BrightDataClient, BrightDataError
from .zones import missing_env_for_lane

DATASET_IDS = {
    "linkedin_person_profile": "gd_l1viktl72bvl7bjuj0",
    "linkedin_company_profile": "gd_l1vikfnt1wgvvqz95w",
    "linkedin_job_listings": "gd_lpfll7v5hcqtkxl6l",
    "linkedin_posts": "gd_lyy3tktm25m4avu764",
    "facebook_profile": "gd_mf0urb782734ik94dz",
    "facebook_posts": "gd_lkaxegm826bjpoo9m5",
    "facebook_post_by_url": "gd_lyclm1571iy3mv57zw",
    "facebook_groups": "gd_lz11l67o2cb3r0lkj3",
    "google_maps_reviews": "gd_lk56epmy2mh7ve4mn",
}


@dataclass(frozen=True)
class DatasetToolMatch:
    tool: str
    dataset_id: str
    reason: str


def dataset_tool_for_url(url: str) -> DatasetToolMatch | None:
    host = (urlparse(url).netloc or "").lower().replace("www.", "")
    path = (urlparse(url).path or "").lower()
    if "linkedin.com" in host:
        if "/company/" in path:
            return DatasetToolMatch("linkedin_company_profile", DATASET_IDS["linkedin_company_profile"], "linkedin company url")
        if "/jobs/" in path or "/jobs/view/" in path:
            return DatasetToolMatch("linkedin_job_listings", DATASET_IDS["linkedin_job_listings"], "linkedin job url")
        if "/posts/" in path:
            return DatasetToolMatch("linkedin_posts", DATASET_IDS["linkedin_posts"], "linkedin post url")
        if "/in/" in path:
            return DatasetToolMatch("linkedin_person_profile", DATASET_IDS["linkedin_person_profile"], "linkedin profile url")
    if "facebook.com" in host:
        if "/groups/" in path:
            return DatasetToolMatch("facebook_groups", DATASET_IDS["facebook_groups"], "facebook group url")
        if "/posts/" in path or re.search(r"/\d{8,}", path):
            return DatasetToolMatch("facebook_post_by_url", DATASET_IDS["facebook_post_by_url"], "facebook post url")
        return DatasetToolMatch("facebook_posts", DATASET_IDS["facebook_posts"], "facebook page/profile url")
    if "google.com" in host and ("/maps/" in path or "maps.google" in host):
        return DatasetToolMatch("google_maps_reviews", DATASET_IDS["google_maps_reviews"], "google maps url")
    return None


def scrape_dataset_url(
    url: str,
    *,
    tool: str | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    missing = missing_env_for_lane("brightdata-dataset")
    if missing:
        raise BrightDataError(f"Missing env: {', '.join(missing)}", error_class="auth")
    match = None
    if tool and tool in DATASET_IDS:
        match = DatasetToolMatch(tool, DATASET_IDS[tool], f"explicit tool {tool}")
    else:
        match = dataset_tool_for_url(url)
    if match is None:
        raise BrightDataError(f"No Bright Data dataset tool matches URL: {url}", error_class="fatal")
    client = BrightDataClient(timeout_seconds=timeout_seconds)
    payload = client.request_json(
        "POST",
        "/datasets/v3/scrape",
        params={"dataset_id": match.dataset_id, "format": "json"},
        body=[{"url": url}],
    )
    if isinstance(payload, dict) and payload.get("snapshot_id"):
        client.poll_snapshot(str(payload["snapshot_id"]))
        payload = client.download_snapshot(str(payload["snapshot_id"]))
    return {
        "url": url,
        "tool": match.tool,
        "dataset_id": match.dataset_id,
        "reason": match.reason,
        "data": payload,
    }


def search_dataset(
    dataset_id: str,
    filter_tree: dict[str, Any],
    *,
    size: int = 10,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    missing = missing_env_for_lane("brightdata-dataset")
    if missing:
        raise BrightDataError(f"Missing env: {', '.join(missing)}", error_class="auth")
    client = BrightDataClient(timeout_seconds=timeout_seconds)
    response = client.request_json(
        "POST",
        "/datasets/filter",
        body={
            "dataset_id": dataset_id,
            "records_limit": max(1, min(size, 10)),
            "filter": filter_tree,
        },
    )
    snapshot_id = None
    if isinstance(response, dict):
        snapshot_id = response.get("snapshot_id") or response.get("id")
    if snapshot_id:
        client.poll_snapshot(str(snapshot_id), attempts=30, delay_seconds=2.0)
        data = client.download_snapshot(str(snapshot_id))
        return {"dataset_id": dataset_id, "filter": filter_tree, "snapshot_id": snapshot_id, "data": data}
    return {"dataset_id": dataset_id, "filter": filter_tree, "data": response}
