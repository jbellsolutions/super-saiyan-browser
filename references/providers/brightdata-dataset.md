# Bright Data Dataset Extractors

**Status in Super Saiyan Browser:** `evaluating` (live adapter — structured scrape/search)  
**Docs:** https://docs.brightdata.com/datasets/scrapers/linkedin/send-first-request  
**Signup:** https://brightdata.com/

## Verified in Super Saiyan Browser

- URL scrape: `POST /datasets/v3/scrape?dataset_id=...`.
- Bulk filter: `POST /datasets/filter` with snapshot polling.
- CLI: `super-browser dataset --url ...` and `--filter-json`.
- Env: `BRIGHTDATA_API_KEY`.
- Flags: `supports_structured_extract`, `supports_anti_bot`.

## Use alone when

- LinkedIn/Facebook/Google Maps URL maps to a known dataset tool.
- Bulk LinkedIn people/company filter search is requested.

## Escalation rank

**1** — preferred when URL matches a supported platform extractor.

## Last verified

2026-06-17 — adapter contract tests; live tests user-gated.
