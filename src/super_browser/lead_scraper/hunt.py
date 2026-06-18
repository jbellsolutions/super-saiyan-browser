from __future__ import annotations

from typing import Any

from .executors.directories import fetch_directory_page, run_directory_serp
from .executors.facebook_groups import run_facebook_group, run_facebook_page
from .executors.google_dorks import run_google_dorks
from .executors.linkedin import run_linkedin_url
from .executors.meta_ad_library import run_meta_ad_library


SOURCE_EXECUTORS = {
    "google_dorks": lambda niche, **_: {"queries": run_google_dorks([f"{niche} companies", f"{niche} directory"])} ,
    "linkedin": lambda niche, url=None, **_: run_linkedin_url(url) if url else {"status": "skipped", "reason": "linkedin requires url"},
    "facebook_groups": lambda niche, url=None, **_: run_facebook_group(url) if url else {"status": "skipped", "reason": "facebook_groups requires url"},
    "meta_ad_library": lambda niche, url=None, **_: run_meta_ad_library(url) if url else {"queries": run_google_dorks([f"{niche} site:facebook.com/ads/library"])} ,
    "directories": lambda niche, **_: {"serp": run_directory_serp([f"{niche} directory", f"{niche} list"])} ,
}


def run_hunt(niche: str, *, sources: list[str] | None = None, dry_run: bool = False) -> dict[str, Any]:
    selected = sources or list(SOURCE_EXECUTORS.keys())
    plan = {"niche": niche, "sources": selected, "dry_run": dry_run, "results": {}}
    if dry_run:
        plan["status"] = "planned"
        return plan
    for source in selected:
        executor = SOURCE_EXECUTORS.get(source)
        if not executor:
            plan["results"][source] = {"status": "unknown_source"}
            continue
        try:
            plan["results"][source] = executor(niche)
        except Exception as exc:
            plan["results"][source] = {"status": "failed", "error": str(exc)}
    plan["status"] = "complete"
    return plan
