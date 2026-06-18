# Setup walkthrough — share this with a new teammate

Repo: [https://github.com/jbellsolutions/super-saiyan-browser](https://github.com/jbellsolutions/super-saiyan-browser)

## Hey, here's how this works

Super Saiyan Browser is a **routing layer** for browser and computer automation:

1. You describe a goal in **plain language** (extract a page, log in, scrape behind anti-bot, draft a post, fetch JSON).
2. Super Saiyan Browser **classifies** the task and runs **3–5 deliberation loops** to pick the cheapest provider that can actually do the job.
3. **Risky external writes** (posts, DMs, purchases, CRM changes) stop at **human approval**.
4. The runtime **executes** with primary + fallback providers and saves **artifacts** under `.super-browser/`.
5. **Verify** checks run reports and artifacts before anyone claims success.

You do **not** choose Hyperbrowser vs Steel vs Playwright manually for every task. You do **not** paste API keys into chat — copy `.env.example` to `.env` locally.

Give an agent this prompt:

> Install Super Saiyan Browser from `https://github.com/jbellsolutions/super-saiyan-browser`. Run `./scripts/super-browser setup`, follow every step, install skills and MCP, run doctor, then use `super-browser-orchestrator` for browser tasks. Plan before run. Wait for `deliberation_complete`. Never paste API keys into chat — use `.env` locally.

Machine-readable steps (same content as this doc):

```bash
./scripts/super-browser setup
./scripts/super-browser setup --client cursor   # optional: tailor commands
```

MCP equivalent: `setup_walkthrough` — returns `welcome` plus numbered steps. Use that as the **first message** when someone drops the GitHub link into Claude Code, Codex, or Cursor.

---

## Step 1 — Clone

```bash
git clone https://github.com/jbellsolutions/super-saiyan-browser.git
cd super-saiyan-browser
```

## Step 2 — Install Python package + Chromium

```bash
python3 -m pip install -e ".[playwright,mcp]"
python3 -m playwright install chromium
```

Playwright is the free local lane. MCP extras enable the JSON tool server.

## Step 3 — Point the runtime at this checkout

```bash
export SUPER_BROWSER_REPO_ROOT="$(pwd)"
```

Add that line to your shell profile if you want it permanent.

## Step 4 — Create `.env` from the template

```bash
cp .env.example .env
```

Edit `.env` locally. **Do not commit `.env` and do not paste secrets into chat.**

## Step 5 — Get API keys (signup links)

| Provider | Env var | Sign up | What it unlocks |
| --- | --- | --- | --- |
| Browser Use Cloud | `BROWSER_USE_API_KEY` | [cloud.browser-use.com](https://cloud.browser-use.com/) | Anti-bot cloud browser, profiles, recordings |
| Bright Data | `BRIGHTDATA_API_KEY` | [brightdata.com](https://brightdata.com/) | Web Unlocker, SERP, datasets, Scraping Browser |
| Hyperbrowser | `HYPERBROWSER_API_KEY` | [hyperbrowser.ai](https://www.hyperbrowser.ai/) | Cloud scrape jobs at scale |
| Airtop | `AIRTOP_API_KEY` | [airtop.ai](https://www.airtop.ai/) | Cloud sessions, page-query, GTM workflows |
| Steel | `STEEL_API_KEY` | [steel.dev](https://steel.dev/) | Hosted Chromium over Playwright CDP |
| Orgo | `ORGO_API_KEY` | [orgo.ai](https://orgo.ai/) | Full desktop / computer-use VMs |
| Decodo (optional) | `DECODO_PROXY` | [decodo.com](https://decodo.com/) | Residential proxy for raw HTTP |
| Browserbase (optional, docs-only) | `BROWSERBASE_API_KEY` | [browserbase.com](https://www.browserbase.com/) | Documented for Stagehand/Model Gateway — **no live adapter yet** ([audit](../references/providers/browserbase-capability-audit.md)) |

Local Playwright and direct raw HTTP work **without** paid keys.

Check what's still missing:

```bash
./scripts/super-browser env-checklist
./scripts/super-browser doctor
```

## Step 6 — Bright Data zones (optional)

If you use Bright Data lanes, set `BRIGHTDATA_API_KEY` in `.env`, then:

```bash
./scripts/super-browser brightdata-discover --write-env
```

This fills unlocker, SERP, and browser zone names automatically. See [brightdata-specialist](../skills/brightdata-specialist/SKILL.md).

## Step 7 — Install skills

**Cursor**

```bash
./scripts/super-browser install-skill --target ~/.cursor/skills --force
```

**Codex**

```bash
./scripts/super-browser install-skill --target ~/.codex/skills --force
```

**Claude Code**

```bash
./scripts/super-browser install-skill --target ~/.claude/skills --force
```

Or use the bundled `.claude-plugin/plugin.json` / `.codex-plugin/plugin.json` and set `SUPER_BROWSER_REPO_ROOT` to this repo.

## Step 8 — Wire MCP

**Cursor / Hermes**

```bash
./scripts/super-browser init-mcp --path ~/.cursor/mcp.json --merge --cwd "$(pwd)"
```

Restart the IDE so the MCP server loads.

## Step 9 — Doctor + first plan

```bash
./scripts/super-browser doctor
./scripts/super-browser plan --goal "Extract the page title from https://example.com"
```

Confirm the JSON includes `council_report.deliberation_complete: true` before running anything that mutates external state.

## Step 10 — Optional: Chrome extension

Load the in-tab list scraper (no API server required):

1. Open `chrome://extensions` → **Developer mode** → **Load unpacked** → select `extension/`
2. Log into your target site, open the results page, open the side panel
3. **Detect table** → **Run scrape** → **Export CSV**

Full guide: [chrome-extension.md](chrome-extension.md)

## Step 11 — Full verification (optional but recommended)

```bash
./scripts/verify-super-browser
```

## How deliberation fits in

| Loops | When |
| --- | --- |
| 3 | Straightforward task — one obvious provider path |
| 5 | Council mode — multiple cloud providers could work |

Read the plan's `council_report` for provider order, cost estimate, `execution_pattern`, and optional `combo_steps`. SSOT docs: [references/providers/](../references/providers/) and [combo-playbook.md](../references/combo-playbook.md).

## Natural-language workflow for agents

1. `setup_walkthrough` or `./scripts/super-browser setup` on first contact.
2. `plan_browser_task` / `super-browser plan` for every new goal.
3. If `awaiting_approval`, wait for human `approve_browser_run` with reason.
4. `run_browser_task` / `super-browser run` only after plan is complete.
5. `verify_browser_run` before reporting success.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| MCP tools missing | Re-run `init-mcp --merge`, restart IDE |
| `doctor` fails on keys | Expected until you add paid providers; local Playwright still works |
| Run blocked "deliberation incomplete" | Re-plan with `--deliberation-rounds 5` |
| Browserbase in plan but not executed | Docs-only by design — use live path from `documented_recommendations` |

More: [agent-quickstart.md](agent-quickstart.md) · [chrome-extension.md](chrome-extension.md) · [README](../README.md)
