# Orgo API Reference (Super Saiyan Browser integration)

Orgo provisions full Linux desktop computers (browser + filesystem + shell) that AI
agents control over HTTP. Super Saiyan Browser uses Orgo as its desktop escalation lane and
drives it through the OpenAI-compatible computer-use endpoint.

- Base URL: `https://www.orgo.ai/api` (override with `ORGO_API_BASE`)
- Auth: `Authorization: Bearer sk_live_...` on every request (`ORGO_API_KEY`)
- Full upstream docs: <https://docs.orgo.ai> (machine index: <https://docs.orgo.ai/llms.txt>)

## API key scopes

| Scope | Behavior |
|---|---|
| Account-wide (default) | Can access every workspace owned by the account. |
| Workspace-scoped | Locked to one workspace; other workspaces return `403` with `code: "workspace_scope_mismatch"`. |

## Resource hierarchy

```
User → Workspaces → Computers
```

### Workspaces

| Endpoint | Notes |
|---|---|
| `POST /workspaces` | Body `{"name": "..."}`. Returns the workspace object with `id` (UUID). |
| `GET /workspaces` | List workspaces. |
| `GET /workspaces/{id}` | Workspace detail, including its computers. |
| `DELETE /workspaces/{id}` | Delete. |

**Schema quirk (observed live, 2026-06):** `/projects` is a deprecated alias for
`/workspaces`, and the live API still returns the workspace list under a `"projects"`
key. Workspace detail returns computers under a `"desktops"` key. Our
`_orgo_collection` helper in `src/super_browser/adapters.py` therefore accepts key
tuples — `("workspaces", "projects")` and `("computers", "desktops")` — plus the
generic `data`/`items` fallbacks.

### Computers

| Endpoint | Notes |
|---|---|
| `POST /computers` | Required: `workspace_id`, `name`. Optional: `os` (linux), `cpu` (1/2/4/8/16), `ram` (4/8/16/32/64 GB), `disk_size_gb`, `resolution` (default `1280x720x24`), `auto_stop_minutes`. |
| `GET /computers/{id}` | Computer detail. Includes stable `instance_id` (`fly_instance_id` is a deprecated alias). |
| `DELETE /computers/{id}` | Delete. |
| `POST /computers/{id}/start` / `/stop` / `/restart` | Lifecycle. |
| `GET`/`PATCH /computers/{id}/auto-stop` | Read/update `auto_stop_minutes` (`0` disables). |
| `POST /computers/{id}/clone` | Copy with full disk state. |
| `PATCH /computers/{id}/resize` | Live hot-resize `vcpus`, `mem_gb`, `disk_size_gb` (grow only), `bandwidth_limit_mbps`. `207` = partial success, `422` = all dimensions rejected. |
| `PATCH /computers/{id}/move` | Move to another workspace (`workspace_id`; `project_id` deprecated alias). |
| `GET /computers/{id}/vnc-password` | VNC credential. |

Computer `status` values: `creating`, `starting`, `running`, `stopping`, `stopped`,
`suspended` (plan downgrade put the account over its limit), `restarting`,
`deleting`, `error`.

### Per-action control endpoints

For custom agent loops (not used by our adapter, which delegates the loop to Orgo):

- `GET /computers/{id}/screenshot` → base64 PNG
- `POST /computers/{id}/click` (`x`, `y`; optional `button`, `double`)
- `POST /computers/{id}/drag` (`start_x`, `start_y`, `end_x`, `end_y`)
- `POST /computers/{id}/type` (`text`), `POST .../key` (`key`, e.g. `ctrl+c`)
- `POST /computers/{id}/scroll` (`direction`; optional `amount`)
- `POST /computers/{id}/wait` (`seconds` 0–60)
- `POST /computers/{id}/bash` (`command`), `POST .../exec` (Python `code`)
- WebSocket: terminal / audio / events at `wss://www.orgo.ai/desktops/{instance_id}/ws/...`
- RTMP streaming: `POST /computers/{id}/stream/start|stop`, `GET .../stream/status`
- Files: `POST /files/upload`, `GET /files`, `POST /files/export`, `GET /files/download`, `DELETE /files/delete`

## Computer-use agent endpoint (what our adapter calls)

`POST /v1/chat/completions` — OpenAI-compatible. Add `computer_id` to the request
body and the chosen model runs a full computer-use loop on that computer.

```json
{
  "model": "claude-sonnet-4-6",
  "computer_id": "<uuid>",
  "messages": [{"role": "user", "content": "<task prompt>"}],
  "stream": false
}
```

Supported models: `claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-4-6`
(default; override with `ORGO_MODEL`), `claude-haiku-4-5`. For non-Claude providers,
run your own loop with the per-action endpoints instead.

## Adapter resolution flow (`_orgo_resolve_computer_id`)

`ORGO_COMPUTER_ID` is **optional**. The adapter resolves a computer in this order:

1. **Pinned** — if `ORGO_COMPUTER_ID` is set, use it as-is.
2. **Workspace lookup** — `GET /workspaces`; prefer a workspace named
   `super-browser` (`ORGO_DEFAULT_WORKSPACE_NAME`), else the first workspace, else
   `POST /workspaces` to create `super-browser`.
3. **Reuse running** — `GET /workspaces/{id}`; among `status == "running"`
   computers, prefer one named `super-browser-agent`
   (`ORGO_DEFAULT_COMPUTER_NAME`), else any running computer.
4. **Start stopped** — if computers exist but none are running, `POST
   /computers/{id}/start` on the named (or first) one.
5. **Create** — `POST /computers` with `workspace_id`, name
   `super-browser-agent`, and `auto_stop_minutes: 30`
   (`ORGO_AUTO_STOP_MINUTES`) so idle desktops shut themselves down.

Any failure surfaces as `Orgo computer discovery failed: <detail>` in the run
report, with the HTTP response body included (our `_http_json` appends it to
`HTTPError` for debuggability).

## Error model and known gates

All errors return `{"error": "..."}`.

| Status | Meaning |
|---|---|
| 400 | Bad JSON / missing field / out-of-range value |
| 401 | Missing or invalid API key |
| 403 | Authenticated but not allowed — **plan limit** or workspace-scope mismatch |
| 404 | Resource not found or no access |
| 409 | Conflict (e.g. stopping a stopped computer) |
| 422 | Validation failed (resize: all dimensions rejected) |
| 429 | Rate limited — exponential backoff (1s start, double, max 60s) |

**Paid-plan gate (observed live):** `POST /computers` on a free account returns
`403` with a "Creating a computer requires a paid plan" body. The adapter's
auto-creation path (step 5) is blocked until the account is upgraded or a computer
is created manually in the Orgo dashboard; steps 1–4 still work against existing
computers.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `ORGO_API_KEY` | yes | Bearer credential. |
| `ORGO_API_BASE` | no | API base override (default `https://www.orgo.ai/api`). |
| `ORGO_MODEL` | no | Computer-use model (default `claude-sonnet-4-6`). |
| `ORGO_COMPUTER_ID` | no | Pin a specific computer; skips auto-discovery entirely. |
