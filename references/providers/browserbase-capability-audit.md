# Browserbase capability audit (2026-06-11)

**Verdict: HARD FAIL on shipping a Super Saiyan Browser adapter today.** Keep `stability: docs-only`. Revisit when Stagehand/Model Gateway workflow classes are in scope.

## Question

Should Super Saiyan Browser wire a live Browserbase adapter now, or stay on Hyperbrowser + Steel + Browser Use for execution?

## Method

Compared Super Saiyan Browser’s **live adapters** (`hyperbrowser`, `steel`, `browser-use`, `playwright`) against Browserbase’s documented platform surface, using vendor docs only — not marketing claims without a source.

## What Browserbase uniquely offers (documented)

| Surface | Super Saiyan Browser today | Source |
| --- | --- | --- |
| **Model Gateway** — one Browserbase API key routes LLM calls for Stagehand (OpenAI, Anthropic, Google) at market token price | No equivalent; agents bring their own LLM outside browser adapters | [Model Gateway overview](https://docs.browserbase.com/platform/model-gateway/overview) |
| **Stagehand v3** — AI-native selectors, self-healing actions, managed action caching on Browserbase-hosted browsers | Partial overlap via `browser-use` agent loops, not Stagehand SDK | [Stagehand reference](https://docs.stagehand.dev/v3/references/stagehand) |
| **Hosted web agents / Functions** — serverless agent deploy on Browserbase infra | No in-repo deploy path | [Browserbase agents use cases](https://docs.browserbase.com/use-cases/agents) |
| **Stealth + session persistence + CDP connect** | `browser-use` (anti-bot), `hyperbrowser` (scrape/stealth), `steel` (CDP) | [Browserbase docs](https://docs.browserbase.com/) |

Model Gateway explicitly **requires** `env: "BROWSERBASE"` — it does not run on local browsers ([Model Gateway overview](https://docs.browserbase.com/platform/model-gateway/overview)).

## What Super Saiyan Browser tasks need today

| Workflow class | Covered without Browserbase? |
| --- | --- |
| Public read / extraction | Yes — `playwright`, then cloud fallbacks |
| Anti-bot / CAPTCHA | Yes — `browser-use` (primary), `hyperbrowser`, `steel` |
| REST scrape + geo proxy | Yes — `hyperbrowser` adapter |
| Playwright CDP on hosted Chromium | Yes — `steel` adapter |
| Raw HTTP + optional proxy | Yes — `decodo-http` |
| Full desktop VM | Yes — `orgo` |

## Gaps Browserbase would fill (future, not blocking)

1. **Stagehand-native goals** — user explicitly wants Stagehand SDK semantics (natural-language selectors, managed caching) on Browserbase infra.
2. **Model Gateway-only billing** — team refuses separate OpenAI/Anthropic/Google keys and wants a single Browserbase invoice for browser + LLM ([blog](https://www.browserbase.com/blog/model-gateway)).
3. **Browserbase Functions / hosted agent deploy** — goal is deploy-on-Browserbase, not route-through-Super-Browser.

None of these are implemented in `src/super_browser/adapters.py` today. Adding Browserbase as “another CDP cloud” would **duplicate Steel** without live-test evidence.

## Redundancy check (deliberation loop 2)

| If we added Browserbase adapter now | Result |
| --- | --- |
| Default anti-bot read | Still routes `browser-use` first — Browserbase adds no win |
| Scrape/markdown jobs | `hyperbrowser` already has REST scrape adapter |
| CDP automation | `steel` already creates session + CDP URL |
| Stagehand / Model Gateway | Requires **new** adapter contract (Stagehand harness), not a thin session API |

## Adapter ship criteria (all required)

- [ ] New workflow class `stagehand_browserbase` or `hosted_agent` in `live-test-matrix.md`
- [ ] Live tests pass with `BROWSERBASE_API_KEY`
- [ ] Deliberation can promote Browserbase to primary without duplicating hyperbrowser/steel on same task shape
- [ ] Cost model row in `references/cost-model.md`
- [ ] Specialist skill `browserbase-specialist` (optional until evaluating)

Until then: **`documented_recommendations` only** in `council_report`.

## Recommendation

| Action | Decision |
| --- | --- |
| Ship Browserbase adapter now | **No** |
| Keep docs-only + deliberation mentions | **Yes** |
| Default execution for “most cloud agent work” | **browser-use** / **hyperbrowser** / **steel** per task shape |
| When user names Browserbase/Stagehand/BYOK | Surface `documented_recommendations`; suggest equivalent live path or custom Stagehand harness outside adapter |

## One-liner for planners

Browserbase wins on **Stagehand + Model Gateway + hosted agent hosting**, not on generic browse/scrape/CDP — Super Saiyan Browser already has live lanes for those. Adapter stays gated.
