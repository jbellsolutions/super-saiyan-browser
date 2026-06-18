# Steel

**Status in Super Saiyan Browser:** `evaluating` (live adapter — Playwright CDP)  
**Docs:** https://docs.steel.dev/  
**Signup:** https://steel.dev/

## Verified in Super Saiyan Browser

- Creates session via `POST https://api.steel.dev/v1/sessions` unless `STEEL_CDP_URL` override set.
- CDP URL uses API-returned `session.id` (not Super Saiyan Browser `run_id`).
- Playwright `connect_over_cdp`, screenshot + text artifacts.
- Profile persist via `persistProfile` / `profileId` when task profile set.
- Env: `STEEL_API_KEY`; optional `STEEL_API_BASE`, `STEEL_CDP_URL`.
- Flags: `supports_anti_bot`, CDP session, proxy, fleet, captcha, auth, profiles.

## Vendor capabilities (claimed)

| Capability | Super Saiyan Browser |
| --- | --- |
| Playwright CDP | **Verified** adapter |
| Puppeteer CDP | Same connect pattern (manual) |
| Selenium | Vendor-supported; not in Super Saiyan Browser adapter |
| Computer-use / BYOM | Documented combo with external agent loop |
| Stealth / proxy / captcha | Session create body options (vendor) |

**Sources:** [Steel Playwright Python](https://docs.steel.dev/overview/guides/connect-with-playwright-python), [Steel docs](https://docs.steel.dev/)

## Use alone when

- Hosted Chromium with Playwright control is the right shape.
- Selectors, screenshots, navigation loops — not REST scrape.
- Agent-friendly infrastructure without full desktop VM.

## Combine when

- External Claude/OpenAI computer-use drives a Steel session (see combo playbook).
- Browserbase agent + Steel Selenium only when goal explicitly requires both.

## Do not use when

- REST scrape output is sufficient (use `hyperbrowser`).
- Full desktop/OS required (`orgo`).
- Workflow lacks live-test proof.

## Overlap matrix

| vs Hyperbrowser | Steel = CDP; Hyperbrowser = scrape. Deliberation loop 4 picks shape. |
| vs Browserbase | Browserbase for hosted agents/BYOK; Steel for CDP/Selenium surface. |
| vs Playwright local | Prefer local when anti-bot/auth absent. |

## Escalation rank

**3** — hosted Chromium after rank 2 cloud scale.

## Last verified

2026-06-11 — adapter code review; specialist doc aligned to REST session id.
