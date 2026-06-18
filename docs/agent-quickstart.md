# Agent quickstart — drop in the GitHub link

Repo: [https://github.com/jbellsolutions/super-saiyan-browser](https://github.com/jbellsolutions/super-saiyan-browser)

## First message to the user

When someone shares this repo, start with:

> **Hey, here's how this works.** Super Saiyan Browser is a routing layer for browser and computer automation. You describe a goal in plain language; Super Saiyan Browser classifies the task, runs **3–5 deliberation loops** to pick the cheapest capable provider (Playwright, Browser Use, Hyperbrowser, Steel, Orgo, or raw HTTP), stops risky external writes for human approval, executes with fallbacks, and verifies artifacts before claiming success. API keys live in `.env` on the machine — never in chat.

Then run setup and follow every step:

```bash
git clone https://github.com/jbellsolutions/super-saiyan-browser.git && cd super-saiyan-browser
./scripts/super-browser setup
```

The `setup` command returns the same `welcome` text plus numbered steps (or use MCP `setup_walkthrough`).

## One-liner for any agent

> Clone `https://github.com/jbellsolutions/super-saiyan-browser`, run `./scripts/super-browser setup`, follow each step (skills, MCP, `.env` keys), run `doctor`, then use the `super-browser-orchestrator` skill for all browser and computer automation. **Plan** before **run**. Do not execute until `deliberation_complete` is true in the plan. Stop for approval on external writes.

Step-by-step guide: [setup-walkthrough.md](setup-walkthrough.md).

## What you get

| Surface | Purpose |
| --- | --- |
| **Plugin** (`.claude-plugin/` or `.codex-plugin/`) | Skills + MCP in one package for Claude Code / Codex |
| **Skills** (`skills/`) | Orchestrator, planner, verifier, provider specialists |
| **MCP** (`mcp/super-browser-server`) | JSON tools: plan, run, approve, profiles, fleet, `setup_walkthrough` |
| **Python CLI** (`super-browser`) | Same runtime without MCP — scripts and humans |

There is no separate HTTP API. Agents use **MCP tools** or the **CLI** (stdout JSON).

## Deliberation (before every run)

| Mode | Loops | When |
| --- | --- | --- |
| Direct | 3 | Single clear provider path |
| Council | 5 | Multiple cloud providers could work |

The plan includes `council_report.deliberation_complete`. **Do not call `run` until it is true.**

Provider SSOT: [references/providers/](../references/providers/) · Combos: [combo-playbook.md](../references/combo-playbook.md) · Browserbase is **docs-only** until [audit](../references/providers/browserbase-capability-audit.md) criteria are met.

## Fastest path (Claude Code / Codex plugin)

```bash
git clone https://github.com/jbellsolutions/super-saiyan-browser.git
cd super-saiyan-browser
export SUPER_BROWSER_REPO_ROOT="$(pwd)"
pip install -e ".[playwright,mcp]" && playwright install chromium
cp .env.example .env   # fill keys locally — never commit .env
./scripts/super-browser doctor
```

Point your agent client at `.claude-plugin/plugin.json` or `.codex-plugin/plugin.json`. The plugin loads `skills/` and `.mcp.json` automatically.

## Fastest path (Cursor / any MCP client)

```bash
git clone https://github.com/jbellsolutions/super-saiyan-browser.git
cd super-saiyan-browser
pip install -e ".[playwright,mcp]" && playwright install chromium
./scripts/super-browser install-skill --target ~/.cursor/skills --force
./scripts/super-browser init-mcp --path ~/.cursor/mcp.json --merge --cwd "$(pwd)"
```

Restart Cursor so MCP picks up `super-browser`.

## Fastest path (copy bundle only — no git checkout)

```bash
./scripts/super-browser install-skill --target ~/.codex/skills --force
```

Run from a cloned repo, or from `pip install super-browser` (uses packaged `share/super-browser` assets).

## Natural-language examples (after setup)

| You say | Agent does |
| --- | --- |
| "Extract product names from https://example.com" | `plan` → `run` on cheapest provider (usually Playwright) |
| "Read my dashboard using profile `ig-account-1`" | `profiles` + `run --profile ig-account-1` |
| "Post a LinkedIn comment" | `run` → stops at `awaiting_approval` → you `approve` with reason |
| "Run the same read on 5 accounts" | `run --fleet 5 --profile base-acct` |
| "Fetch this JSON endpoint cheaply" | Routes to `decodo-http` when goal implies raw HTTP |
| "Use Stagehand on Browserbase" | Deliberation surfaces docs-only path; suggest live equivalent or custom Stagehand harness |

## Skill to invoke

**`super-browser-orchestrator`** — owns plan → approval → execute → verify.

Supporting skills: `super-browser-planner`, `super-browser-verifier`, `publishing-safety-specialist`, and per-provider specialists (`playwright-specialist`, `steel-specialist`, etc.).

## Verify before you trust it

```bash
./scripts/verify-super-browser
```

## Weekly provider intelligence (maintainers)

```bash
python3 scripts/weekly-provider-intelligence.py              # dry run report
python3 scripts/weekly-provider-intelligence.py --apply --verify   # update cache + SSOT stamps
```

GitHub Action: `.github/workflows/weekly-provider-intelligence.yml` (Mondays, auto-commit when configured).

## Printing Press CLI (separate artifact)

The in-repo **Python CLI** ships with the plugin. A **Printing Press Go CLI** (installable binary + MCP) is a planned second distribution channel — see `docs/printing-press-cli.md`. Until that is published, use the Python CLI or MCP tools above.
