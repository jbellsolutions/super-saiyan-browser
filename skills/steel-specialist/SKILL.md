---
name: steel-specialist
description: "Escalation rank 3 (hosted Chromium): Evaluate and use Steel for cloud browser sessions" and agent browser infrastructure. Use as an evaluating provider until live Super Saiyan Browser tests prove task-specific reliability.
---

# Steel Specialist

## Use For

- Cloud browser sessions to evaluate.
- Agent-focused browser infrastructure.
- Playwright CDP control against Steel-hosted browser sessions.
- Fallbacks where Browser Use or Playwright are not a fit.

## Do Not Use For

- Production jobs before live test coverage exists.
- Raw HTTP endpoints.
- Full desktop work.

## Required Env

- `STEEL_API_KEY`
- Optional: `STEEL_CDP_URL`

## Super Saiyan Browser Adapter

The adapter uses Playwright's CDP connection:

1. Creates a Steel session via REST (`POST /v1/sessions`) and builds `wss://connect.steel.dev?apiKey=...&sessionId=<steel-session-id>` unless `STEEL_CDP_URL` is set.
2. Connects with Playwright Python.
3. Navigates to the requested URL.
4. Captures screenshot, body text, and metadata.

This requires the Playwright Python package in the local runtime.

If `STEEL_CDP_URL` is set, Super Saiyan Browser validates it before connecting. Loopback WS/HTTP is allowed for local testing, but private-network/link-local endpoints and insecure remote WS/HTTP require explicit override env vars.

Before connecting to a Steel CDP session, Super Saiyan Browser resolves public-looking target hostnames locally. If DNS points to loopback, private-network, or link-local addresses, execution returns `provider_url_resolved_target_scope` and does not open the provider session. The CDP path also installs Super Saiyan Browser's request target-scope guard before navigation. Browser redirects or subresources into `loopback`, `private_network`, `link_local`, or `local_file` from a different planned scope return a `browser_request_target_scope` blocked result rather than page artifacts. Treat both as non-resumable safety stops; create a new run or replan for the intended target scope.

## Verification

- Contract-tested with fake Playwright CDP objects.
- Live-gated by `STEEL_API_KEY`.
- Keep status evaluating until live traces prove reliability for the task class.

Docs:

- https://docs.steel.dev/integrations/playwright
- https://docs.steel.dev/overview/guides/connect-with-playwright-python
- https://docs.steel.dev/overview/guides/playwright-python
