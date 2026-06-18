---
name: airtop-specialist
description: "Escalation rank 2 (cloud scale): Use Airtop for cloud browser sessions," page-query extraction, no-code or webhook-driven browser agents, scheduled GTM workflows, and business-user-maintained automations. Use when Airtop's agent/session model is a better operational fit than a local browser runtime.
---

# Airtop Specialist

## Use For

- Cloud browser sessions where Super Saiyan Browser should open a URL and ask Airtop to query the page.
- Scheduled monitoring and GTM workflows.
- No-code agent workflows maintained by non-developers.
- Webhook-triggered browser agents.

## Do Not Use For

- MCP-native local agent workflows without a wrapper.
- Simple local tests.
- High-volume extraction before live cost is measured.
- Cases where provider behavior has not been live-tested.

## Required Env

- `AIRTOP_API_KEY`
- `AIRTOP_AGENT_ID` and `AIRTOP_WEBHOOK_ID` for webhook workflows.
- Optional: `AIRTOP_API_BASE`, `AIRTOP_TIMEOUT_MINUTES`

## Super Saiyan Browser Adapter

The adapter uses Airtop REST APIs:

1. `POST /sessions` to create a cloud session.
2. `POST /sessions/{sessionId}/windows` to open the target URL.
3. `POST /sessions/{sessionId}/windows/{windowId}/page-query` with the task prompt.
4. `DELETE /sessions/{sessionId}` to terminate the session.

It writes `airtop-output.json` with the session, window, and query payloads.

If `AIRTOP_API_BASE` is set, Super Saiyan Browser validates it before sending `AIRTOP_API_KEY`. Loopback HTTP is allowed for local testing, but private-network/link-local endpoints and insecure remote HTTP require explicit override env vars.

Before opening a target URL in Airtop, Super Saiyan Browser resolves public-looking hostnames locally. If DNS points to loopback, private-network, or link-local addresses, execution returns `provider_url_resolved_target_scope` and does not create the Airtop session. Treat that as a non-resumable safety stop; create a new run or replan for the intended target scope.

## Verification

- Contract-tested with fake Airtop REST responses.
- Live-gated by `AIRTOP_API_KEY`.
- Mark task class stable only after the run report contains Airtop output and cost/usage data.

Docs:

- https://docs.airtop.ai/api-reference/airtop-api/sessions/create
- https://docs.airtop.ai/api-reference/airtop-api/windows/create
- https://docs.airtop.ai/api-reference/airtop-api/windows/page-query
- https://docs.airtop.ai/api-reference/airtop-api/sessions/terminate
