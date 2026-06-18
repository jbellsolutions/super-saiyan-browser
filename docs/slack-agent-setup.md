# Slack agent setup

Super Saiyan Browser's optional Slack daemon (`super-browser agent`) is a thin ingress: Slack message → plan/run → approval replies in-thread. It reuses the same runtime as CLI and MCP — no second codebase.

## Tokens you need

| Variable | Format | What it is |
| --- | --- | --- |
| `SLACK_BOT_TOKEN` | `xoxb-...` | Bot User OAuth Token — sends messages, reads DMs |
| `SLACK_APP_TOKEN` | `xapp-...` | App-Level Token with `connections:write` — powers Socket Mode |

Optional:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SUPER_BROWSER_SLACK_EXECUTE` | `false` | If `true`, `approve` in Slack also dispatches the provider immediately |

Install the Slack SDK:

```bash
pip install slack-bolt
```

## Create the Slack app (one-time)

1. Open [https://api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**.
2. Name it (e.g. `Super Saiyan Browser`) and pick your workspace.

### Enable Socket Mode

1. **Settings → Socket Mode** → toggle **ON**.
2. When prompted, create an **App-Level Token** with scope `connections:write`.
3. Copy the token → this is `SLACK_APP_TOKEN` (`xapp-...`).

Socket Mode means you do **not** need a public URL or Request URL for events.

### Bot scopes

**OAuth & Permissions → Scopes → Bot Token Scopes** — add:

| Scope | Why |
| --- | --- |
| `app_mentions:read` | Respond when @mentioned in channels |
| `chat:write` | Post replies |
| `im:history` | Read DM history |
| `im:read` | Open DM channel |
| `im:write` | Send DMs |

### Subscribe to events

**Event Subscriptions** → toggle **ON** (Socket Mode still uses this list).

Under **Subscribe to bot events**, add:

- `app_mention`
- `message.im`

Save changes.

### Install to workspace

**Install App → Install to Workspace** → allow.

Copy **Bot User OAuth Token** → this is `SLACK_BOT_TOKEN` (`xoxb-...`).

## Configure Super Saiyan Browser

```bash
cd /path/to/super-browser
cp .env.example .env
```

Add to `.env` (or export in shell):

```bash
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token
SUPER_BROWSER_SLACK_EXECUTE=false
```

`SUPER_BROWSER_SLACK_EXECUTE=false` is recommended: Slack `approve` records approval only; you resume execution from CLI/MCP when ready. Set `true` only if you want one-shot approve-and-run from Slack.

## Run the daemon

```bash
pip install -e ".[playwright]" slack-bolt   # plus any provider keys you need
source .env
./scripts/super-browser agent
```

Or with auto-execute on approve:

```bash
./scripts/super-browser agent --execute-on-approve
```

## Invite the bot

In Slack: `/invite @Super Saiyan Browser` to a channel, or DM the bot directly.

## Message protocol

| Message | Action |
| --- | --- |
| Plain text goal | Creates a run (plan + durable state) |
| `status run_abc123` | Shows run summary |
| `approve run_abc123 looks good` | Records approval |
| `deny run_abc123 not now` | Records denial |
| `resume run_abc123` | Resumes after approval |

External writes still hit the approval gate — the daemon does not bypass publishing safety.

## Hermes alternative (no standalone daemon)

If you use **agent-os** / Hermes, wire the `browser_operator` identity and Super Saiyan Browser MCP instead of running `super-browser agent`. Slack then goes through Hermes; the browser runtime is the same.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN` | Export both tokens before starting |
| `slack_bolt is required` | `pip install slack-bolt` |
| Bot never responds in channel | Invite bot; mention with `@Super Saiyan Browser` |
| Bot never responds in DM | Confirm `message.im` event + `im:*` scopes |
| Socket Mode disconnects | Regenerate `SLACK_APP_TOKEN` with `connections:write` |
