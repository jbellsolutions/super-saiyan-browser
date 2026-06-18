# Bright Data SERP API

**Status in Super Saiyan Browser:** `evaluating` (live adapter — dedicated SERP lane)  
**Docs:** https://docs.brightdata.com/scraping-automation/serp-api/introduction  
**Signup:** https://brightdata.com/

## Verified in Super Saiyan Browser

- Adapter: `POST https://api.brightdata.com/request` with SERP zone and search URL.
- CLI: `super-browser serp --query "..."`.
- Env: `BRIGHTDATA_API_KEY`, `BRIGHTDATA_SERP_ZONE`.
- Flags: `supports_serp`.

## Use alone when

- Task is a search-engine query, not a page URL.
- Google dorks, directory discovery, competitor SERP mining.

## Escalation rank

**-2** — dedicated SERP lane, never browser fallbacks.

## Last verified

2026-06-17 — adapter contract tests; live tests user-gated.
