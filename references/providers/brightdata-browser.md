# Bright Data Scraping Browser

**Status in Super Saiyan Browser:** `evaluating` (live adapter — Playwright CDP)  
**Docs:** https://docs.brightdata.com/scraping-automation/scraping-browser/five-minute-how-to  
**Signup:** https://brightdata.com/

## Verified in Super Saiyan Browser

- Adapter: Playwright `connect_over_cdp` to `wss://...@brd.superproxy.io:9222`.
- Captures screenshot, text, HTML, metadata artifacts.
- Env: `BRIGHTDATA_BROWSER_USERNAME`, `BRIGHTDATA_BROWSER_PASSWORD` (or customer id + zone + password).
- Flags: `supports_anti_bot`, `supports_auth`, `supports_long_running`, `supports_captcha`.

## Use alone when

- Unlocker returns challenge/empty pages.
- Task needs pagination, forms, or JS rendering.

## Escalation rank

**2** — alongside Hyperbrowser/Airtop until live tests pass.

## Last verified

2026-06-17 — adapter contract tests; live tests user-gated.
