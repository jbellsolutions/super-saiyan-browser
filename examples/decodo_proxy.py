#!/usr/bin/env python3
"""
Decodo Proxy Integration — Raw HTTP and browser proxy patterns.

Decodo residential proxies ($2/GB, sticky 10-min sessions) for:
1. Raw API calls through residential IPs
2. Cost optimization vs Browser Use Cloud's $5/GB
3. Playwright-based scraping when you control the browser stack
4. Geo-targeting with specific country exit nodes

⚠️ DOES NOT WORK against Facebook headless detection.
For Meta/LinkedIn/Cloudflare-protected sites, use rank 1 (Browser Use Cloud).

Proxy details:
    Host: us.decodo.com
    Ports: 10001-10007 (round-robin for IP rotation)
    Auth: spo2nwl1tw:***
    Type: Residential, sticky 10-minute sessions
    Cost: $2/GB
"""

import os
import sys
import json
import requests
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROXY_HOST = "us.decodo.com"
PROXY_PORTS = [10001, 10002, 10003, 10004, 10005, 10006, 10007]
PROXY_USER = os.environ.get("DECODO_USER", "YOUR_DECODO_USER")
PROXY_PASS = os.environ.get("DECODO_PASSWORD", "YOUR_DECODO_PASSWORD")

# Round-robin port for IP rotation
import itertools
_port_cycle = itertools.cycle(PROXY_PORTS)


def get_proxy_url() -> str:
    """Get a proxy URL with rotated port."""
    port = next(_port_cycle)
    return f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{port}"


def get_proxy_dict() -> dict:
    """Get proxy dict for requests library."""
    proxy_url = get_proxy_url()
    return {"http": proxy_url, "https": proxy_url}


# ---------------------------------------------------------------------------
# Pattern 1: curl + Decodo (Shell)
# ---------------------------------------------------------------------------

CURL_PATTERN = """
# Single request through Decodo
curl -x "http://{user}:{password}@{host}:{port}" \\
  "https://api.target.com/data" \\
  -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" \\
  -H "Accept: application/json"

# With IP rotation (different port = different exit IP)
curl -x "http://{user}:{password}@{host}:10001" "https://api.target.com/endpoint1"
curl -x "http://{user}:{password}@{host}:10002" "https://api.target.com/endpoint2"

# POST with JSON body
curl -x "http://{user}:{password}@{host}:10001" \\
  -X POST "https://api.target.com/submit" \\
  -H "Content-Type: application/json" \\
  -d '{{"key": "value"}}'
""".format(user=PROXY_USER, password=PROXY_PASS, host=PROXY_HOST, port=PROXY_PORTS[0])


# ---------------------------------------------------------------------------
# Pattern 2: Python requests + Decodo
# ---------------------------------------------------------------------------

def requests_through_decodo(url: str, headers: dict = None) -> requests.Response:
    """
    Simple GET request through Decodo residential proxy.

    Args:
        url: Target URL
        headers: Optional custom headers

    Returns:
        requests.Response object
    """
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    if headers:
        default_headers.update(headers)

    proxies = get_proxy_dict()

    resp = requests.get(
        url,
        headers=default_headers,
        proxies=proxies,
        timeout=30,
    )

    print(f"  [{resp.status_code}] {url} (via {proxies['http'].split('@')[1]})")
    return resp


def post_through_decodo(url: str, data: dict, headers: dict = None) -> requests.Response:
    """POST request through Decodo proxy."""
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json",
    }

    if headers:
        default_headers.update(headers)

    proxies = get_proxy_dict()

    resp = requests.post(
        url,
        json=data,
        headers=default_headers,
        proxies=proxies,
        timeout=30,
    )

    return resp


# ---------------------------------------------------------------------------
# Pattern 3: Playwright + Decodo + Stealth
# ---------------------------------------------------------------------------

PLAYWRIGHT_DECODO_PATTERN = """
import asyncio
from playwright.async_api import async_playwright


async def playwright_through_decodo(url: str):
    \"\"\"
    Playwright with Decodo residential proxy + stealth.

    ⚠️ WARNING: This does NOT bypass Facebook's headless detection.
    Tested June 2026 against Meta Ad Library — failed (blank page / API splash).
    Works for sites without aggressive anti-bot protection.
    \"\"\"
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            proxy={{
                "server": "http://{host}:{port}",
                "username": "{user}",
                "password": "{password}",
            }}
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/132.0.0.0 Safari/537.36"
            ),
            viewport={{"width": 1920, "height": 1080}},
            locale="en-US",
        )

        page = await context.new_page()

        # Add stealth scripts (requires playwright-stealth)
        # from playwright_stealth import stealth_sync
        # await stealth_sync(page)

        await page.goto(url, wait_until="networkidle")
        content = await page.content()

        await browser.close()
        return content


# Run it
asyncio.run(playwright_through_decodo("https://example.com"))
""".format(host=PROXY_HOST, port=PROXY_PORTS[0], user=PROXY_USER, password=PROXY_PASS)


# ---------------------------------------------------------------------------
# Pattern 4: Browser Use Open-Source + Decodo
# ---------------------------------------------------------------------------

BROWSER_USE_DECODO_PATTERN = """
# ⚠️ Browser Use open-source has NO built-in stealth and NO direct proxy support.
# Proxy support is only available through Browser Use Cloud (use_cloud=True).
# For local + Decodo, use Playwright directly (Pattern 3).
# For anti-detection, use Browser Use Cloud with built-in proxies (rank 1).

from browser_use.agent.service import Agent
from browser_use import Browser

# Option A: Use Browser Use Cloud (stealth + built-in proxy)
browser = Browser(
    use_cloud=True,                    # Cloud = hardened Chromium
    cloud_proxy_country_code="us",     # Residential proxy
    headless=False,
)

# Option B: Local open-source (NO stealth, NO proxy)
# browser = Browser(headless=False)

agent = Agent(
    task="Go to https://example.com and extract the main content",
    llm="anthropic/claude-sonnet-4-6",
    browser=browser,
)

async def main():
    result = await agent.run()
    print(result)

import asyncio
asyncio.run(main())
"""


# ---------------------------------------------------------------------------
# Pattern 5: Batch API Calls with IP Rotation
# ---------------------------------------------------------------------------

def batch_with_rotation(urls: list[str]) -> list[dict]:
    """
    Make multiple API calls, each through a different Decodo exit IP.

    Rotating ports = rotating exit IPs = looking like different users.
    """
    results = []

    for i, url in enumerate(urls):
        port = PROXY_PORTS[i % len(PROXY_PORTS)]  # Round-robin ports
        proxy_url = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{port}"

        try:
            resp = requests.get(
                url,
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=30,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            results.append({
                "url": url,
                "port": port,
                "status": resp.status_code,
                "size": len(resp.content),
            })
            print(f"  [{resp.status_code}] {url[:60]}... (port {port})")

        except Exception as e:
            results.append({"url": url, "port": port, "error": str(e)})
            print(f"  [ERR] {url[:60]}... (port {port}): {e}")

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("DECODO PROXY PATTERNS")
    print("=" * 60)

    # Show the curl pattern
    print("\n📋 Pattern 1: curl + Decodo")
    print("-" * 40)
    print(CURL_PATTERN[:500] + "...")

    # Test a real request
    print("\n📋 Pattern 2: Python requests + Decodo")
    print("-" * 40)
    try:
        resp = requests_through_decodo("https://httpbin.org/ip")
        print(f"  Your exit IP: {resp.json().get('origin', 'unknown')}")
    except Exception as e:
        print(f"  ❌ Request failed: {e}")

    # Show the Playwright pattern
    print("\n📋 Pattern 3: Playwright + Decodo")
    print("-" * 40)
    print(PLAYWRIGHT_DECODO_PATTERN[:300] + "...")

    # Show the Browser Use pattern
    print("\n📋 Pattern 4: Browser Use open-source + Decodo")
    print("-" * 40)
    print(BROWSER_USE_DECODO_PATTERN[:300] + "...")

    print("\n" + "=" * 60)
    print("⚠️  Reminder: Decodo does NOT bypass Facebook headless detection.")
    print("   For Meta/LinkedIn/Cloudflare: use Browser Use Cloud (rank 1).")
    print("   Decodo is for raw HTTP, Playwright fallback, and cost optimization.")
    print("=" * 60)
