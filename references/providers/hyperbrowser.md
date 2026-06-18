# Hyperbrowser

**Status in Super Saiyan Browser:** `evaluating` (live adapter — REST scrape)  
**Docs:** https://docs.hyperbrowser.ai/  
**Signup:** https://www.hyperbrowser.ai/

## Verified in Super Saiyan Browser

- Adapter: `POST /api/scrape` → poll status → fetch result → `hyperbrowser-output.json`.
- `useStealth` follows task anti-bot classification.
- Proxy: opt-in via `HYPERBROWSER_USE_PROXY` or task proxy hint.
- Env: `HYPERBROWSER_API_KEY`; optional `HYPERBROWSER_API_BASE`, poll/timeout overrides.
- Flags: `supports_anti_bot`, `supports_proxy_injection`, `supports_fleet`, profiles, captcha, auth, long-running.

## Vendor capabilities (claimed)

| Capability | Super Saiyan Browser |
| --- | --- |
| REST scrape (markdown/html/links) | **Verified** adapter |
| HyperAgent / AI agent | Not in adapter — use specialist docs for manual harness |
| Geo-targeted proxies (country/city) | Proxy injection + vendor session options (live-test) |
| BYO proxy | Task `proxy` hint / env |
| Cloud sessions | Partial via scrape `sessionOptions` |

**Source:** [Hyperbrowser docs](https://docs.hyperbrowser.ai/)

## Use alone when

- Async scrape job with structured output is enough.
- Geo-targeted public extraction at scale.
- Anti-bot read tasks where stealth scrape beats spinning up CDP.

## Combine when

- Rare: scrape fails → fallback to `steel` CDP (deliberation fallback ladder, not parallel clouds).

## Do not use when

- Playwright CDP control or Selenium is required (use `steel`).
- Production workflow without live-test evidence for that task class.
- Task is raw HTTP only (`decodo-http`).

## Overlap matrix

| vs Steel | Hyperbrowser = scrape API; Steel = CDP/Selenium. Prefer one. |
| vs Browserbase | Browserbase = hosted agents/Stagehand; Hyperbrowser = scrape/scale. |
| vs Browser Use | Browser Use wins hardened anti-bot agent loops; Hyperbrowser wins cheap scrape jobs. |

## Escalation rank

**2** — cloud scale tie-breaker after rank 1.

## Last verified

2026-06-11 — adapter code review; live tests user-gated.
