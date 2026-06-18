---
name: brightdata-specialist
description: "Bright Data final add-in: Web Unlocker, SERP API, dataset extractors, and Scraping Browser for lead-gen and anti-bot read workflows."
---

# Bright Data Specialist

## What Are Zones?

Bright Data **zones** are named product instances in your account (for example `mcp_unlocker` for Web Unlocker, `mcp_browser` for Scraping Browser). You do **not** need to look them up manually — Super Saiyan Browser discovers active zones from the Bright Data API.

## Setup (minimal)

1. Set `BRIGHTDATA_API_KEY` in `.env`, **or** connect the Bright Data MCP in Cursor (Super Saiyan Browser reuses that token).
2. Run:

```bash
super-browser brightdata-discover --write-env
```

That fills unlocker/SERP/browser zone names and the browser zone password automatically. SERP works through your unlocker zone when you have no dedicated SERP zone.

**Browser lane only:** paste `BRIGHTDATA_BROWSER_USERNAME` or `BRIGHTDATA_CUSTOMER_ID` from [Account settings](https://brightdata.com/cp/setting/customer_details) — the API does not expose the full CDP username.

## Use For

- SERP mining and Google dork workflows (`brightdata-serp`).
- One-shot anti-bot URL unlock (`brightdata-unlocker`).
- Structured LinkedIn/Facebook/Google Maps extraction (`brightdata-dataset`).
- JS-heavy fallback browsing (`brightdata-browser`).

## CLI Shortcuts

```bash
super-browser brightdata-discover
super-browser serp --query "commercial cleaning companies Texas"
super-browser unlock --url "https://example.com/protected"
super-browser dataset --url "https://www.linkedin.com/company/acme"
super-browser hunt --niche "B2B SaaS founders" --dry-run
```

## Routing Order

1. Dataset tool when URL matches a supported platform extractor.
2. SERP lane for search queries without page URLs.
3. Unlocker for anti-bot read-only URLs.
4. Scraping Browser for interaction/JS fallback.

## Do Not Use For

- Production claims before live-test evidence exists.
- External writes without approval.
- Tasks where free Playwright or raw HTTP already suffice.
