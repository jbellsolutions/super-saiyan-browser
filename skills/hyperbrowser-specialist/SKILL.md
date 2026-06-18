---
name: hyperbrowser-specialist
description: "Escalation rank 2 (cloud scale): Evaluate and use Hyperbrowser for cloud browser automation" when its API and pricing fit the workflow. Use as an evaluating provider until live Super Saiyan Browser tests prove reliability for a task class.
---

# Hyperbrowser Specialist

## Use For

- Cloud browser automation experiments.
- REST scrape jobs where markdown, HTML, or link output is enough.
- Scale-oriented browser workflows after live tests.
- Fallback evaluation when Browser Use is not ideal.

## Do Not Use For

- Production flows not yet live-tested.
- External writes without approval.
- Jobs where a stable provider already solves the task cheaply.

## Required Env

- `HYPERBROWSER_API_KEY`
- Optional: `HYPERBROWSER_API_BASE`, `HYPERBROWSER_USE_PROXY`, `HYPERBROWSER_TIMEOUT_MS`, `HYPERBROWSER_POLL_ATTEMPTS`, `HYPERBROWSER_POLL_SECONDS`

## Super Saiyan Browser Adapter

The adapter calls Hyperbrowser REST:

1. `POST /api/scrape` with `url`, `sessionOptions`, and `scrapeOptions`.
2. Treats the returned `jobId` as an async handle, not a completed scrape.
3. Polls `GET /api/scrape/{jobId}/status` until the job is complete, failed, stopped, or timed out.
4. Fetches `GET /api/scrape/{jobId}` only after a completed status, then writes `hyperbrowser-output.json`.

Default output requests markdown, HTML, and links. `useStealth` follows Super Saiyan Browser's anti-bot classification, and proxy use is opt-in with `HYPERBROWSER_USE_PROXY`.

If the status endpoint remains pending/running after the configured attempts, or returns failed/stopped/unknown status, the adapter marks the attempt failed so fallback routing can continue. A bare `jobId` response is never treated as task success.

If `HYPERBROWSER_API_BASE` is set, Super Saiyan Browser validates it before sending `HYPERBROWSER_API_KEY`. Loopback HTTP is allowed for local testing, but private-network/link-local endpoints and insecure remote HTTP require explicit override env vars.

Before submitting a target URL to Hyperbrowser, Super Saiyan Browser resolves public-looking hostnames locally. If DNS points to loopback, private-network, or link-local addresses, execution returns `provider_url_resolved_target_scope` and does not create the scrape job. Treat that as a non-resumable safety stop; create a new run or replan for the intended target scope.

## Verification

- Contract-tested with fake job creation, status polling, result fetch, failed-status, and unfinished-job responses.
- Live-gated by `HYPERBROWSER_API_KEY`.
- Keep status evaluating until each target workflow has live artifacts and cost data.

Docs:

- https://docs.hyperbrowser.ai/reference/api-reference/scrape
- https://docs.hyperbrowser.ai/reference/sdks/python/scrape
