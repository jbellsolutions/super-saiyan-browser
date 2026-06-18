# Super Saiyan Browser Combo Playbook

Use **one capable provider** by default. Combos are for documented splits where two surfaces genuinely improve outcomes — not to stack clouds because both exist.

## Decision order

1. Can `playwright` or `decodo-http` do it alone?
2. Can a **single** cloud provider (`browser-use`, `hyperbrowser`, `steel`, `airtop`) do it alone?
3. Only then consider a combo from this playbook.

Planner deliberation loops 4–5 enforce this order. See `references/providers/*.md` for per-vendor detail.

## Recipes

### Hyperbrowser geo scrape (single provider)

**When:** Public extraction with country/city proxy, markdown/HTML/links output, no CDP clicks.

**Use alone:** `hyperbrowser` REST scrape job.

**Do not pair with:** Steel or Browserbase unless scrape fails and a CDP retry is justified.

### Steel Playwright CDP (single provider)

**When:** Hosted Chromium, selectors, screenshots, multi-step automation via Playwright.

**Use alone:** `steel` session create + CDP connect (Super Saiyan Browser adapter).

**Do not pair with:** Hyperbrowser scrape for the same page load.

### Steel + external computer-use (combo, documented)

**When:** You need a **real browser surface** driven by Claude/OpenAI computer-use or a custom agent loop with your own LLM keys.

**Pattern:**

1. Steel hosts the browser session (CDP or Selenium).
2. Your agent loop issues actions; Steel persists the session.
3. Super Saiyan Browser records artifacts and approval gates for external writes.

**Runtime note:** Super Saiyan Browser executes step 1 when `steel` is primary. Step 2 is outside the adapter unless you wire a custom agent harness.

**Sources:** [Steel docs](https://docs.steel.dev/), [Steel Playwright Python](https://docs.steel.dev/overview/guides/connect-with-playwright-python).

### Browserbase + Steel (combo, documented-only for Browserbase)

**When:** Legacy Selenium selectors on Steel **plus** Browserbase-hosted agent orchestration, Stagehand, or Model Gateway BYOK — tasks that mention both vendors explicitly.

**Pattern:**

1. Browserbase for session/agent/LLM gateway (docs-only until adapter ships).
2. Steel for Selenium or CDP control where Steel is stronger.

**Default:** Do **not** combine unless the goal names both or deliberation loop 4 flags `execution_pattern: combo`.

**Sources:** [Browserbase agents](https://docs.browserbase.com/use-cases/agents), [Steel Selenium](https://docs.steel.dev/).

### Anti-bot escalation ladder (single path per run)

**Order (one primary, ordered fallbacks):**

1. `playwright` — ordinary public pages, deterministic selectors.
2. `browser-use` — Cloudflare, CAPTCHA, logged-in cloud profiles.
3. `hyperbrowser` (stealth scrape) **or** `steel` (CDP session) — only if step 2 unavailable or task shape demands scrape vs CDP.

Do not schedule all three clouds for one read job.

### Browserbase as default cloud recommendation (documented)

**When:** Hosted web agents, Stagehand, Functions, Model Gateway BYOK, stealth sessions with observability — and `BROWSERBASE_API_KEY` is set **after** adapter ships.

**Until adapter ships:** Planner returns `documented_recommendations` in `council_report`; execution stays on `hyperbrowser` / `steel` / `browser-use` equivalents.

**Sources:** [Browserbase documentation](https://docs.browserbase.com/).

## Approval and verification

Any combo that includes external writes, credentials, or publishing still requires approval gates from `references/security-and-approval-policy.md`. Verifier checks `execution_pattern` and `combo_steps` in `council_report`.
