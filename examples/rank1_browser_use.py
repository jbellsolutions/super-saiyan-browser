#!/usr/bin/env python3
"""
Escalation rank 1: Browser Use Cloud — anti-bot browser automation (paired with Playwright for local work).

Hardened Chromium fork that passes CreepJS and BrowserLeaks.
Cloudflare bypass, PerimeterX bypass, Meta Ad Library — this handles what nothing else can.

Prerequisites:
    pip install browser-use-sdk
    export BROWSER_USE_API_KEY="bu_live_..."

Usage:
    python rank1_browser_use.py
"""

import os
import sys
import json
from typing import Optional
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = os.environ.get("BROWSER_USE_API_KEY", "bu_live_YOUR_KEY_HERE")

# ---------------------------------------------------------------------------
# Structured Output Models (Pydantic)
# ---------------------------------------------------------------------------

class Advertiser(BaseModel):
    """An advertiser found in Meta Ad Library."""
    name: str
    page_url: str
    ad_count: Optional[int] = None


class ProductInfo(BaseModel):
    """Product information extracted from an e-commerce site."""
    name: str
    price: str
    url: str
    in_stock: bool = True


# ---------------------------------------------------------------------------
# Client Setup
# ---------------------------------------------------------------------------

def get_client():
    """Initialize the Browser Use Cloud client."""
    from browser_use_sdk.v3 import BrowserUse

    client = BrowserUse(api_key=API_KEY)
    print("✓ Browser Use Cloud client initialized")
    return client


# ---------------------------------------------------------------------------
# Pattern 1: Simple Task (unstructured output)
# ---------------------------------------------------------------------------

def scrape_meta_ad_library(client, keyword: str = "bath remodel"):
    """
    Scrape Meta Ad Library for advertisers using a keyword.

    This is the pattern that works against Facebook's aggressive anti-bot
    detection. The hardened Chromium + residential proxy combo bypasses
    what Playwright + Decodo cannot.
    """
    from browser_use_sdk.v3 import BuModel, ProxyCountryCode

    print(f"\n🔍 Searching Meta Ad Library for: '{keyword}'")

    # Create a session — this provisions a hardened Chromium in the cloud
    session = client.sessions.create(
        model=BuModel.claude_sonnet_4_6,          # Best for complex web tasks
        proxy_country_code=ProxyCountryCode.US,    # US residential proxy
        keep_alive=True,                           # Keep alive for follow-up tasks
    )
    print(f"  Session created: {session.id}")

    try:
        # Run the task — natural language, no brittle selectors
        result = client.run(
            session_id=session.id,
            task=f"""
            Go to https://www.facebook.com/ads/library/
            In the search box, type "{keyword}" and press Enter.
            Wait for results to load.
            
            Scroll through the results and extract:
            - Advertiser name
            - Their Facebook page URL
            - How many ads they're running (if visible)
            
            Return the data as a JSON array. Get at least 20 advertisers.
            """,
        )

        print(f"  Status: {result.status}")
        print(f"  Output: {result.output[:500]}...")

        return result.output

    finally:
        # ALWAYS stop sessions — they cost $0.02/hr
        client.sessions.stop(session_id=session.id)
        print(f"  Session stopped")


# ---------------------------------------------------------------------------
# Pattern 2: Structured Output (Pydantic)
# ---------------------------------------------------------------------------

def extract_structured_data(client, url: str, task: str):
    """
    Extract data with automatic Pydantic validation.

    Browser Use Cloud's v3 agent returns typed Python objects
    when you pass `output_schema`.
    """
    from browser_use_sdk.v3 import BuModel

    print(f"\n📊 Extracting from: {url}")

    session = client.sessions.create(
        model=BuModel.claude_sonnet_4_6,
        proxy_country_code=None,  # No proxy needed for non-protected sites
        keep_alive=False,
    )

    try:
        result = client.run(
            session_id=session.id,
            task=task,
            output_schema=ProductInfo,  # ← Returns list[ProductInfo]
        )

        # result.output is now a list of ProductInfo objects
        if isinstance(result.output, list):
            for item in result.output:
                print(f"  {item.name}: {item.price} [{item.url}]")

        return result.output

    finally:
        client.sessions.stop(session_id=session.id)


# ---------------------------------------------------------------------------
# Pattern 3: CDP WebSocket (Direct Browser Control)
# ---------------------------------------------------------------------------

async def cdp_direct_control(client):
    """
    Connect Playwright/Puppeteer/Selenium directly to Browser Use's cloud
    browsers via CDP WebSocket. Full programmatic control without the AI agent.
    """
    from browser_use_sdk.v3 import ProxyCountryCode
    from playwright.async_api import async_playwright

    print("\n🔌 Connecting via CDP WebSocket...")

    # Get a CDP URL from Browser Use
    browser_session = client.browsers.create(
        proxy_country_code=ProxyCountryCode.US,
        keep_alive=True,
    )
    cdp_url = browser_session.cdp_url
    print(f"  CDP URL: {cdp_url[:60]}...")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        page = browser.contexts[0].pages[0]

        await page.goto("https://www.facebook.com/ads/library/")
        await page.wait_for_timeout(3000)

        # Extract with vanilla Playwright
        advertisers = await page.evaluate("""
            const links = document.querySelectorAll('a[href*="/"]');
            const results = new Set();
            links.forEach(a => {
                const href = a.getAttribute('href');
                if (href && !href.includes('facebook.com/ads')) {
                    results.add(JSON.stringify({
                        name: a.textContent.trim(),
                        url: href
                    }));
                }
            });
            return [...results].map(JSON.parse);
        """)

        print(f"  Found {len(advertisers)} advertisers via CDP")
        return advertisers


# ---------------------------------------------------------------------------
# Pattern 4: Cron-Ready (minimal output)
# ---------------------------------------------------------------------------

def cron_scrape(target_url: str, task_description: str):
    """
    Minimal script for cron jobs. Captures output to stdout for cron capture.
    Puts the full result in a JSON file for downstream processing.
    """
    import datetime
    from browser_use_sdk.v3 import BrowserUse, BuModel, ProxyCountryCode

    client = BrowserUse(api_key=API_KEY)

    session = client.sessions.create(
        model=BuModel.claude_sonnet_4_6,
        proxy_country_code=ProxyCountryCode.US,
    )

    result = client.sessions.run(
        session_id=session.id,
        task=f"Go to {target_url}. {task_description}. Return JSON.",
    )

    client.sessions.stop(session_id=session.id)

    # Save for downstream
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"/tmp/browser_use_{timestamp}.json"

    with open(output_file, "w") as f:
        json.dump({"output": result.output, "status": str(result.status)}, f)

    print(result.output)  # stdout → captured by cron
    print(f"\nSaved to {output_file}")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if API_KEY == "bu_live_YOUR_KEY_HERE":
        print("❌ Set BROWSER_USE_API_KEY environment variable first.")
        print("   Sign up at https://browser-use.com → Dashboard → API Keys")
        sys.exit(1)

    client = get_client()

    # Run the Meta Ad Library scrape (primary use case)
    scrape_meta_ad_library(client, keyword="bath remodel")

    # Structured extraction example (uncomment to use)
    # extract_structured_data(
    #     client,
    #     url="https://example.com/products",
    #     task="Extract all products with name, price, and URL",
    # )

    print("\n✅ All tasks complete")
