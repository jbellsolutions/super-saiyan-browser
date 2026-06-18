# Browserbase

**Status in Super Saiyan Browser:** `docs-only` (deliberation + SSOT; no live adapter yet)  
**Docs:** https://docs.browserbase.com/  
**Changelog watch:** https://docs.browserbase.com/changelog  
**Signup:** https://www.browserbase.com/

## Verified in Super Saiyan Browser

- Listed in `providers.py` with `stability: docs-only` — excluded from automatic routing until adapter ships.
- Planner `documented_recommendations` when goal mentions Stagehand, Browserbase, Model Gateway, Functions, or BYOK.
- Env: `BROWSERBASE_API_KEY` (documented in `.env.example`).

## Vendor capabilities (claimed — verify on docs)

| Capability | Notes |
| --- | --- |
| Stealth browsers | Basic and advanced stealth tiers for anti-bot workloads |
| Hosted web agents | Fully hosted agents with observability and live view |
| Stagehand | Natural-language browser automation SDK |
| Functions | Serverless browser functions |
| Model Gateway / BYOK | Bring your own LLM keys |
| Session persistence | Context IDs, reusable sessions |
| CDP connect | Connect Playwright/Puppeteer to cloud sessions |

**Source:** [Browserbase agents use cases](https://docs.browserbase.com/use-cases/agents)

## Use alone when

- Task needs Stagehand-native flows, hosted agents, or Model Gateway BYOK.
- You want one vendor for stealth + agent + session persistence + observability.
- Cost-optimized cloud default **after** adapter exists and live tests pass.

## Combine when

- **Steel + Browserbase:** Selenium/CDP on Steel with agent orchestration on Browserbase (see `references/combo-playbook.md`).
- **Not** with Hyperbrowser scrape for the same page — pick scrape **or** CDP.

## Do not use when

- Hyperbrowser REST scrape or Steel CDP already satisfies the task with fewer moving parts.
- No `BROWSERBASE_API_KEY` and adapter is not wired.

## Overlap matrix

| vs Hyperbrowser | Hyperbrowser wins REST scrape + geo proxy jobs today (live adapter). |
| vs Steel | Steel wins Playwright CDP + Selenium in-repo today. Browserbase wins hosted agent/BYOK/Stagehand positioning. |
| vs Browser Use | Browser Use wins anti-bot routing today; Browserbase competes on agent hosting + Model Gateway. |

## Escalation rank

Documented as rank **2** (cloud scale) when adapter ships — same band as Hyperbrowser/Airtop.

## Last verified

2026-06-11 — SSOT initial; adapter gated. Capability audit: [browserbase-capability-audit.md](browserbase-capability-audit.md) (**HARD FAIL** on adapter until Stagehand/Model Gateway workflow class ships).
