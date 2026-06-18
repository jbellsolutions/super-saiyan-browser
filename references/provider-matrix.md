# Super Saiyan Browser Provider Matrix

This matrix is the source of truth for planner and specialist decisions. Mark provider behavior as `verified`, `claimed`, `evaluating`, or `untested`; do not treat vendor claims as proof.

## Escalation ranks (cost tie-breaker, not the routing model)

Capabilities decide which providers can do a job; the escalation rank only orders equally capable providers from cheapest to most expensive. See `references/routing-playbook.md` for the capability table.

| Rank | Providers | Role |
| --- | --- | --- |
| **SERP lane** | `brightdata-serp` | Search engine results only |
| **Separate lane** | `decodo-http` | Raw HTTP/proxy only — never in browser fallbacks |
| **1** | `playwright`, `browser-use`, `brightdata-unlocker`, `brightdata-dataset` | Default browser automation, cheap unlock, structured extractors |
| **2** | `brightdata-browser`, `hyperbrowser`, `airtop`, `browserbase` (docs-only) | Cloud scale — scrape jobs, page-query, GTM automations, hosted agents |
| **3** | `steel` | Hosted Chromium — Playwright CDP control of cloud browser sessions |
| **4** | `orgo` | Full desktop VMs — browser plus files plus terminal |

| Provider | Rank | Status | Best for | Do not use when | Required or optional env |
| --- | --- | --- | --- | --- | --- |
| Local Playwright | 1 | verified-local | Deterministic browser tests, screenshots, known selectors, fixture sites, low-cost extraction | Advanced anti-bot, personal logged-in Chrome sessions, full desktop apps | none; requires Playwright Python package and Chromium runtime |
| Browser Use Cloud | 1 | implemented-live-gated | Hardened cloud browser, anti-bot workflows, profiles, recordings, live URLs | Cheap raw HTTP, local deterministic tests | `BROWSER_USE_API_KEY` |
| Bright Data Unlocker | 1 | evaluating | One-shot anti-bot URL unlock | Browser interaction required | `BRIGHTDATA_API_KEY` (zone auto-discovered) |
| Bright Data Dataset | 1 | evaluating | Structured LinkedIn/Facebook/Maps extractors | Unsupported platform URL | `BRIGHTDATA_API_KEY` |
| Bright Data SERP | SERP lane | evaluating | Google/Bing/Yandex search results | Task is a page URL, not a query | `BRIGHTDATA_API_KEY` (uses unlocker zone if no SERP zone) |
| Bright Data Browser | 2 | evaluating | Playwright CDP scraping browser | Unlocker/dataset already enough | `BRIGHTDATA_BROWSER_USERNAME`, `BRIGHTDATA_BROWSER_PASSWORD` (password auto-discovered) |
| Hyperbrowser | 2 | implemented-live-gated | REST scrape jobs, cloud browser automation to test, scale workflows | Production until live tests pass for the target workflow | `HYPERBROWSER_API_KEY` |
| Airtop | 2 | implemented-live-gated | Cloud sessions, page-query extraction, no-code/browser-agent workflows, scheduled GTM, webhook-driven automations | MCP-native local workflows without a wrapper, untested anti-bot claims, high-volume use before cost is measured | `AIRTOP_API_KEY` |
| Steel | 3 | implemented-live-gated | Playwright CDP control of Steel cloud browser sessions, agent browser infrastructure | Raw HTTP endpoints, full desktop work, production before live task proof | `STEEL_API_KEY` |
| Orgo | 4 | implemented-live-gated | Full computer/desktop automation, browser plus files plus terminal, multi-window workflows | Browser-only or raw HTTP tasks | `ORGO_API_KEY` (optional `ORGO_COMPUTER_ID` pin) |
| Decodo Raw HTTP | separate | verified-pattern | Supplied `http://` or `https://` API endpoints, JSON endpoints, optional residential proxy fetches, cheap bulk data | Missing endpoint, browser rendering, advanced headless detection, personal logged-in sessions | optional `DECODO_PROXY` for residential proxy routing |
| Browserbase | 2 | docs-only | Stagehand, hosted web agents, Model Gateway BYOK, stealth sessions (documented; adapter gated) | Hyperbrowser scrape or Steel CDP already sufficient; no adapter wired | `BROWSERBASE_API_KEY` when adapter ships |

## Provider Selection Notes

- Prefer Playwright first only when the site is ordinary and the workflow is deterministic.
- Treat local Playwright as ready only when `super-browser doctor` reports `ready_local` and `browser_runtime_available=true`; a package-only install without Chromium is `runtime_missing`.
- Prefer Hyperbrowser or Airtop for general cloud browser work at scale.
- Prefer Browser Use when the task mentions Cloudflare, Meta, LinkedIn, PerimeterX, DataDome, CAPTCHA, or repeated prior bot failures.
- Prefer Steel when hosted Chromium sessions with Playwright CDP control are needed.
- Prefer Orgo only when a browser is not enough.
- Prefer Decodo only when no browser rendering is required and the task supplies a concrete HTTP endpoint.
- Direct Decodo/raw HTTP can run without `DECODO_PROXY`; set `DECODO_PROXY` only when residential proxy routing is required.
- Keep Hyperbrowser, Steel, and Airtop behind live tests until task-specific reliability is proven.
- Treat `raw_http_redirect_target_scope`, `raw_http_resolved_target_scope`, `provider_url_resolved_target_scope`, and `browser_request_target_scope` as non-resumable safety stops. Replan for the intended target scope instead of retrying the same public-web run.
- Raw HTTP, URL-capable remote/desktop providers, and Playwright-backed browser guards resolve public-looking hostnames before continuing and block DNS results that point at loopback, private-network, or link-local addresses, plus unresolved public hosts that cannot be verified locally.
- Optional provider API/CDP base overrides are validated before credentials are sent. Loopback HTTP/WS is allowed for self-hosted local providers; private-network/link-local endpoints and insecure remote HTTP/WS require explicit override env vars.

## Implemented Adapter Notes

- Browser Use uses `browser_use_sdk.v3.AsyncBrowserUse().run(...)` and writes the returned SDK payload to `browser-use-output.json`.
- Orgo resolves a computer (pinned `ORGO_COMPUTER_ID`, reused running computer, restarted existing computer, or a newly created `super-browser-agent` with 30-minute auto-stop), submits the desktop task through Orgo computer-use chat completions, and requests a screenshot. Missing screenshot evidence is a failed provider attempt.
- Airtop creates a session, opens a window, runs page-query, writes `airtop-output.json`, and terminates the session.
- Hyperbrowser calls REST `/scrape`, polls `/scrape/{jobId}/status`, fetches `/scrape/{jobId}` after completion, and writes `hyperbrowser-output.json`.
- Steel connects through Playwright CDP to `connect.steel.dev`, captures screenshot/text/metadata, and writes local artifacts.
- Every adapter returns a structured `blocked` result with docs and missing env vars instead of failing silently when setup is incomplete.
- JSON-backed adapters mark explicit provider errors, nested provider-result errors, failed statuses, unfinished statuses after polling, and `success=false` responses as failed attempts with saved output evidence instead of treating any API response as success.
- Playwright-backed adapters install a request target-scope guard before navigation. Local Playwright and Steel CDP block browser redirects or subresources into loopback, private-network, link-local, or local-file targets unless the run was planned for that same target scope.
