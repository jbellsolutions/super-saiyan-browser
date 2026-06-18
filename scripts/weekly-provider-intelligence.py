#!/usr/bin/env python3
"""Fetch vendor changelog fingerprints and deliberate whether SSOT docs need updates."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from super_browser.deliberation import deliberate_weekly_intelligence  # noqa: E402

INTEL_SOURCES = {
    "browserbase": {
        "url": "https://docs.browserbase.com/changelog",
        "ssot": ROOT / "references/providers/browserbase.md",
    },
    "hyperbrowser": {
        "url": "https://docs.hyperbrowser.ai/changelog",
        "ssot": ROOT / "references/providers/hyperbrowser.md",
    },
    "steel": {
        "url": "https://docs.steel.dev/changelog",
        "ssot": ROOT / "references/providers/steel.md",
    },
}

CACHE_DIR = ROOT / "references" / ".provider-intel-cache"
REPORT_PATH = ROOT / "references" / "provider-intelligence-report.json"


def _fetch(url: str, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": "super-browser-weekly-intel/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.txt"


def _stamp_ssot(path: Path, *, excerpt: str) -> None:
    if not path.exists():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    text = path.read_text()
    marker = "<!-- provider-intel-sync:"
    if marker in text:
        before, _, after = text.partition(marker)
        after = after.split("-->", 1)[-1] if "-->" in after else after
        text = before.rstrip() + f"\n\n{marker} {stamp} -->\n" + after.lstrip()
    else:
        text = text.rstrip() + f"\n\n{marker} {stamp} -->\n"
    if excerpt.strip() and "provider-intel-excerpt" not in text:
        text += f"\n<!-- provider-intel-excerpt: {excerpt[:400].replace('-->', '')} -->\n"
    path.write_text(text)


def run(*, apply: bool, verify: bool, commit: bool) -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat(), "providers": []}
    changed_any = False

    for name, meta in INTEL_SOURCES.items():
        cache = _cache_path(name)
        current = cache.read_text() if cache.exists() else ""
        try:
            fetched = _fetch(meta["url"])
        except URLError as exc:
            report["providers"].append({"provider": name, "error": str(exc), "verdict": "fetch_failed"})
            continue

        verdict = deliberate_weekly_intelligence(name, current_summary=current, fetched_summary=fetched)
        entry = {
            "provider": name,
            "url": meta["url"],
            "changed": verdict["apply"],
            "verdict": verdict["verdict"],
            "review_loops": verdict["review_loops"],
        }
        report["providers"].append(entry)

        if verdict["apply"]:
            changed_any = True
            if apply:
                cache.write_text(fetched)
                _stamp_ssot(meta["ssot"], excerpt=fetched[:500])

    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

    if not apply or not changed_any:
        return 0

    if verify:
        result = subprocess.run([str(ROOT / "scripts" / "verify-super-browser")], cwd=ROOT, check=False)
        if result.returncode != 0:
            print("verify-super-browser failed; skipping commit", file=sys.stderr)
            return result.returncode

    if commit:
        subprocess.run(
            ["git", "add", "references/providers", "references/.provider-intel-cache", str(REPORT_PATH)],
            cwd=ROOT,
            check=True,
        )
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                "chore: weekly provider intelligence sync",
            ],
            cwd=ROOT,
            check=True,
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write cache + SSOT sync stamps when content changed.")
    parser.add_argument("--verify", action="store_true", help="Run ./scripts/verify-super-browser after apply.")
    parser.add_argument("--commit", action="store_true", help="Git commit after successful verify (requires --apply).")
    args = parser.parse_args()
    if args.commit and not args.apply:
        parser.error("--commit requires --apply")
    return run(apply=args.apply, verify=args.verify, commit=args.commit)


if __name__ == "__main__":
    raise SystemExit(main())
