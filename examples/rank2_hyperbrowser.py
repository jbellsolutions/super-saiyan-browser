#!/usr/bin/env python3
"""Escalation rank 2 example: Hyperbrowser — cloud scale REST scrape jobs."""

import os
import subprocess
import sys

if __name__ == "__main__":
    if not os.environ.get("HYPERBROWSER_API_KEY"):
        print("Set HYPERBROWSER_API_KEY before running rank-2 Hyperbrowser live tests.")
        sys.exit(1)
    proc = subprocess.run(
        ["super-browser", "live-test", "--provider", "hyperbrowser"],
        check=False,
    )
    sys.exit(proc.returncode)
