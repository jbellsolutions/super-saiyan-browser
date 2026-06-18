#!/usr/bin/env python3
"""Escalation rank 1 example: local Playwright — free deterministic browser work."""

import subprocess
import sys

GOAL = "Open https://example.com and return the page title."
CMD = ["super-browser", "live-test", "--provider", "playwright", "--workflow-class", "local_browser_fixture"]

if __name__ == "__main__":
    proc = subprocess.run(CMD, check=False)
    sys.exit(proc.returncode)
