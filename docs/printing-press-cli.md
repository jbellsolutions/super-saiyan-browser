# Printing Press CLI (planned distribution)

[Printing Press](https://printingpress.dev/) generates ship-ready **Go CLIs** with verify, dogfood, MCP cobratree, and publish flows.

## What exists today

Super Saiyan Browser already ships a **Python CLI** (`super-browser`) and **MCP server** inside the plugin bundle. Agents installed via `install-skill` or the Claude/Codex plugin get this surface automatically.

## What Printing Press adds

A **standalone Go binary** (e.g. `super-browser-pp`) for users who want:

- Single-file / brew-style install without Python on PATH
- Printing Press library distribution ([printing-press-library](https://github.com)) for agent auto-download
- Go-native MCP shim (same pattern as `sendivo` in `~/printing-press/library/`)

The Go CLI would **shell out to** or **embed calls against** the Python runtime (cobratree pattern) — not reimplement routing.

## Build command (when ready)

From a machine with Printing Press installed:

```bash
/printing-press super-browser
```

Research inputs: this repo's CLI subcommands (`plan`, `run`, `profiles`, `approve`, `fleet`, `doctor`), MCP tool schemas in `src/super_browser/mcp_server.py`, and `references/routing-playbook.md`.

## Status

**Not published yet.** Track alongside the main repo release. Until then, use:

- Plugin + skills + MCP (agents)
- `pip install -e .` + `super-browser` (humans/scripts)
