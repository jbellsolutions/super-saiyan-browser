---
name: browser-use-specialist
description: "Escalation rank 1 (default cloud browser): Use Browser Use Cloud for hardened cloud browser automation," anti-bot workflows, browser profiles, recordings, live URLs, and complex natural-language browser tasks. Use when sites are protected or local Playwright is likely to fail.
---

# Browser Use Specialist

## Use For

- Anti-bot or high-risk sites: Meta, LinkedIn, Cloudflare-heavy flows, PerimeterX, DataDome.
- Complex cloud browser tasks where a hardened browser and agent loop are valuable.
- Profiles, recordings, live URLs, and structured extraction.

## Do Not Use For

- Simple local tests where Playwright is enough.
- Raw HTTP endpoints.
- High-volume scraping when a cheaper route is proven reliable.

## Required Env

- `BROWSER_USE_API_KEY`

## Super Saiyan Browser Adapter

The runtime uses `browser_use_sdk.v3.AsyncBrowserUse().run(...)`, saves `browser-use-output.json`, and records live/recording/screenshot URLs when returned by the SDK. If the key or SDK is missing, it returns a structured blocked result with setup instructions.

Before dispatching a target URL to Browser Use, Super Saiyan Browser resolves public-looking hostnames locally. If DNS points to loopback, private-network, or link-local addresses, execution returns `provider_url_resolved_target_scope` and does not call the SDK. Treat that as a non-resumable safety stop; create a new run or replan for the intended target scope.

## MCP Note

Use the current Browser Use MCP docs for the exact auth header and endpoint. Do not copy stale examples without checking docs.

Docs: https://docs.browser-use.com/cloud/guides/mcp-server
