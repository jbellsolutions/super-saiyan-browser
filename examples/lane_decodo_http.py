#!/usr/bin/env python3
"""Separate lane: Decodo raw HTTP — not a browser escalation rank."""

import subprocess
import sys

if __name__ == "__main__":
    proc = subprocess.run(
        [
            "super-browser",
            "live-test",
            "--provider",
            "decodo-http",
            "--workflow-class",
            "raw_http_direct",
        ],
        check=False,
    )
    sys.exit(proc.returncode)
