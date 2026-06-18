#!/usr/bin/env python3
"""Escalation rank 3 example: Steel — agent CDP browser infrastructure."""

import os
import subprocess
import sys

if __name__ == "__main__":
    if not os.environ.get("STEEL_API_KEY"):
        print("Set STEEL_API_KEY before running rank-3 Steel live tests.")
        sys.exit(1)
    proc = subprocess.run(["super-browser", "live-test", "--provider", "steel"], check=False)
    sys.exit(proc.returncode)
