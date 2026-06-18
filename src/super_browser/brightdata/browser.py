from __future__ import annotations

from typing import Any

from .zones import browser_cdp_url, missing_env_for_lane


class BrightDataBrowserError(Exception):
    pass


def scrape_with_browser(
    url: str,
    *,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    missing = missing_env_for_lane("brightdata-browser")
    if missing:
        raise BrightDataBrowserError(f"Missing env: {', '.join(missing)}")
    cdp_url = browser_cdp_url()
    if not cdp_url:
        raise BrightDataBrowserError("Bright Data browser CDP URL could not be constructed")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise BrightDataBrowserError(f"Playwright is required for brightdata-browser: {exc}") from exc

    navigation_timeout_ms = max(1000, timeout_seconds * 1000)
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
        except PlaywrightError as exc:
            raise BrightDataBrowserError(f"Bright Data browser CDP connection failed: {exc}") from exc
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=navigation_timeout_ms)
            title = page.title()
            text = page.locator("body").inner_text(timeout=min(10000, navigation_timeout_ms))
            html = page.content()
            screenshot_bytes = page.screenshot(full_page=True)
        finally:
            try:
                browser.close()
            except Exception:
                pass
    return {
        "url": url,
        "title": title,
        "text": text,
        "html": html,
        "text_length": len(text or ""),
        "html_length": len(html or ""),
        "screenshot_bytes": screenshot_bytes,
    }
