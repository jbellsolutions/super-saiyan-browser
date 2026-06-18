# Deep-Dive: Airtop — Cloud Browser SaaS Platform

> **Provider:** Airtop (`airtop.ai`)  
> **Tier in Stack:** Tier 2 — No-Code SaaS Backup
> **Last Updated:** June 5, 2026

> **Lineup note (June 2026):** Browserbase and Rtrvr were removed from the Super Saiyan Browser lineup after this research was written; comparisons below are historical context. Airtop now sits in Tier 2 alongside Hyperbrowser.

---

## Table of Contents

1. [Platform Overview & Value Proposition](#platform-overview--value-proposition)
2. [Architecture](#architecture)
3. [API & Integration Options](#api--integration-options)
4. [Pricing & Credit Model](#pricing--credit-model)
5. [Comparison with Browser Use Cloud](#comparison-with-browser-use-cloud)
6. [Limitations & When Not to Use](#limitations--when-not-to-use)

---

## Platform Overview & Value Proposition

Airtop is a cloud browser SaaS platform that positions itself as "no-code browser automation for GTM teams." Unlike Browser Use (AI-native, developer-focused) or Browserbase (developer tooling), Airtop targets **business users** who want to build browser agents through a visual interface with pre-built templates.

### Key Differentiators

| Dimension | Airtop | Browser Use Cloud | Browserbase |
|-----------|--------|-------------------|-------------|
| **Target user** | Business/GTM teams | AI agent developers | Developers |
| **Interface** | Visual no-code builder | Python SDK + MCP | Playwright-compatible API |
| **Template library** | ✅ Pre-built GTM agents | ❌ | ❌ |
| **Scheduling** | ✅ Built-in cron | ❌ Must build yourself | ❌ |
| **Compliance** | ✅ SOC2/HIPAA | ❌ | ❌ |
| **Pricing model** | Credit-based monthly | Usage-based (hr + GB) | Usage-based |

### Where Airtop Fits in the Stack

Airtop is Tier 3 — the **no-code backup**. It's not the primary tool (that's Browser Use Cloud for anti-detection, Tier 1) but fills specific gaps:

1. **Non-developer workflows** — Marketing/sales teams can build agents without writing code
2. **Pre-built GTM templates** — Faster than coding from scratch for common patterns (lead enrichment, company research, competitor monitoring)
3. **Scheduled monitoring** — Built-in scheduling vs. coding cron jobs + Browser Use
4. **SOC2/HIPAA compliance** — Enterprise requirements Browser Use Cloud doesn't meet

---

## Architecture

### Cloud-Only, No Self-Hosting

Airtop is **100% cloud-hosted** with no on-premise or self-hosting option. All browser sessions run on Airtop's infrastructure. This simplifies compliance (they handle it) but means:

- No custom browser binaries
- No local development mode
- Data passes through Airtop's servers
- Vendor lock-in for browser infrastructure

### How Browser Sessions Work

```
┌──────────┐     REST API      ┌──────────────┐     Browser     ┌───────────┐
│  Client  │ ────────────────→ │  Airtop API  │ ──────────────→ │  Target   │
│  (curl/  │ ←──────────────── │  (airtop.ai) │ ←────────────── │  Website  │
│  Python) │   JSON results    │              │   page content  │           │
└──────────┘                   └──────────────┘                 └───────────┘
```

1. Client creates a session via REST API (or visual builder)
2. Airtop provisions a cloud browser with the requested configuration
3. Client triggers an "agent" with natural-language instructions
4. Agent navigates, clicks, scrolls, extracts data
5. Results returned via polling or webhook
6. Session is terminated

### Agent Execution Model

Airtop uses its own AI model (not Claude/GPT) for browser control. This means:

- **Lower LLM cost** — included in credits, not billed separately
- **Potential accuracy trade-off** — their model may not be as capable as Claude for complex tasks
- **No model choice** — you can't select which LLM drives the agent

### Proxy Architecture

| Plan | Proxy Type | Custom Proxy |
|------|-----------|:---:|
| Free | None / shared | ❌ |
| Starter ($26/mo) | Built-in residential | ❌ |
| Professional ($170/mo) | Built-in residential | ✅ (Oxylabs, Smartproxy, IPRoyal) |
| Enterprise | Dedicated | ✅ (Any) |

Decodo integration would require the Professional plan ($170/mo), making it a poor fit for cost-optimized proxy usage.

---

## API & Integration Options

### REST API (Primary)

Airtop exposes a REST API at `https://api.airtop.ai/api/`. Key endpoints:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/sessions` | POST | Create cloud browser session |
| `/v1/sessions/{id}` | DELETE | Terminate session |
| `/v1/sessions/{id}/agent` | POST | Trigger agent task |
| `/v1/sessions/{id}/agent/{runId}` | GET | Poll agent status/results |
| `/hooks/agents/{id}/webhooks/{wid}` | POST | Trigger via webhook |
| `/invocations/{id}` | GET | Get webhook invocation results |

### Authentication

```bash
# All requests use Bearer token auth
curl -H "Authorization: Bearer ${AIRTOP_API_KEY}" \
     "https://api.airtop.ai/api/v1/sessions"
```

API keys are managed at `portal.airtop.ai/api-keys`.

### Webhook Support

Airtop supports webhooks for push-based results:

```bash
curl -X POST "https://api.airtop.ai/api/hooks/agents/{agentId}/webhooks/{webhookId}" \
  -H "Authorization: Bearer ${AIRTOP_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"configVars": {"url": "https://target.com"}}'
```

Results are POSTed to the configured webhook URL when the agent completes.

### No MCP Support

As of June 2026, Airtop does not provide an MCP server endpoint. Integration with Hermes Agent requires wrapping the REST API in a skill function (see `tier3_airtop.py`).

### No SDK

Airtop does not provide a Python/Node.js SDK. All integration is via raw REST API calls. This is both a strength (no dependency lock-in) and a weakness (more boilerplate).

### Visual Builder (No-Code)

Airtop's primary interface is a visual agent builder at `portal.airtop.ai`:

- Drag-and-drop step configuration
- Pre-built templates for common GTM tasks
- Scheduled execution (hourly, daily, weekly)
- Results viewable in dashboard
- No programming required

This is the main value prop for business users — but not relevant for programmatic AI agent integration.

---

## Pricing & Credit Model

### Plans

| Plan | Price/mo | Credits | Sessions | Custom Proxy | Compliance |
|------|----------|---------|----------|:---:|:---:|
| **Free** | $0 | 1,000 | 1 | ❌ | ❌ |
| **Starter** | $26 | 30K-150K | 3 | ❌ | ❌ |
| **Professional** | $170 | 300K-1.5M | 10 | ✅ | ✅ SOC2 |
| **Enterprise** | Custom | Custom | Custom | ✅ | ✅ SOC2/HIPAA |

### Credit Consumption

Credits are consumed based on:
- **Browser session time** — per-minute billing
- **Agent steps** — each action (navigate, click, type) costs credits
- **Data extraction volume** — larger pages or more extracted data costs more
- **Proxy usage** — residential proxy costs more credits than datacenter

Exact credit rates are not publicly documented (requires login to portal). Estimates based on starter plan:
- 30K credits = ~60-100 agent runs of moderate complexity
- ~$0.26-0.43 per agent run at the starter tier

### Cost Comparison (per 100 Agent Tasks)

| Provider | Estimated Cost | Notes |
|----------|---------------|-------|
| Airtop Starter | **~$26-43** | 100 simple tasks, ~30K credits |
| Airtop Professional | **~$170** | Higher limits, custom proxy |
| Browser Use Cloud | **~$75-150** | $0.75/task avg, includes LLM |
| Browserbase | **~$0** | Free tier, but fragile at scale |
| Rtrvr + BYOK | **~$0** | Free, your own Gemini key |

---

## Comparison with Browser Use Cloud

| Dimension | Browser Use Cloud | Airtop | Verdict |
|-----------|:---:|:---:|---------|
| **Anti-detection** | ⭐⭐⭐⭐⭐ Hardened Chromium | ⭐⭐⭐ Built-in, less proven | Browser Use wins |
| **API quality** | ⭐⭐⭐⭐⭐ SDK + CDP + MCP + REST | ⭐⭐ REST only | Browser Use wins |
| **LLM quality** | ⭐⭐⭐⭐⭐ Claude, GPT choice | ⭐⭐⭐ Proprietary, no choice | Browser Use wins |
| **No-code interface** | ❌ | ⭐⭐⭐⭐⭐ Visual builder | Airtop wins |
| **Scheduling** | ❌ Manual cron | ⭐⭐⭐⭐⭐ Built-in | Airtop wins |
| **Compliance** | ❌ | ⭐⭐⭐⭐ SOC2/HIPAA | Airtop wins |
| **Template library** | ❌ | ⭐⭐⭐⭐⭐ Pre-built GTM | Airtop wins |
| **Structured output** | ⭐⭐⭐⭐⭐ Pydantic/Zod | ⚠️ JSON | Browser Use wins |
| **Cost per task** | $0.50-1.50 | $0.26-0.43 (simple) | Airtop wins |
| **Custom proxy** | Enterprise only | Professional+ | Airtop wins (lower tier) |

### Decision Matrix

```
Is the site anti-bot protected (Meta, Cloudflare, PerimeterX)?
  ├─ YES → Browser Use Cloud (Airtop's anti-detection is unproven)
  └─ NO ↓

Do you need SOC2/HIPAA compliance?
  ├─ YES → Airtop
  └─ NO ↓

Do you need scheduled recurring tasks with a no-code builder?
  ├─ YES → Airtop
  └─ NO ↓

Do you need structured output (Pydantic models)?
  ├─ YES → Browser Use Cloud
  └─ NO ↓

Are you cost-sensitive for simple, non-anti-bot tasks?
  ├─ YES → Airtop (lower per-task cost)
  └─ NO → Browser Use Cloud (better overall quality)
```

---

## Limitations & When Not to Use

### 1. Unproven Anti-Detection Against Meta

Airtop claims bot-detection bypass, but this was **not empirically tested** against Meta Ad Library in our June 2026 testing. Browser Use Cloud's hardened Chromium is the proven choice for Meta.

### 2. No MCP Support

Cannot be used as an MCP server in Hermes Agent. Requires wrapping REST API calls in skill functions, which adds complexity and loses MCP's standardized tool discovery.

### 3. Proprietary Agent Model

You cannot choose the LLM driving the agent. If Airtop's model is less capable than Claude/GPT for your task, you're stuck. Browser Use Cloud lets you choose from multiple models.

### 4. No CDP/Playwright Access

You cannot connect Playwright or Puppeteer directly to Airtop browsers. All interaction goes through their agent model. This limits advanced use cases requiring direct browser control.

### 5. Limited Customization

- No custom browser profiles
- No cookie/localStorage persistence across sessions
- No file upload/download to browser workspace
- No JavaScript injection or CDP commands

### 6. Credit Opacity

Credit consumption rates are not publicly documented, making cost prediction difficult. You won't know the exact cost of a task until you run it.

### 7. Vendor Lock-In

- No self-hosting option
- No open-source core
- Templates are platform-specific (can't export)
- Proprietary agent model (can't replicate elsewhere)

### 8. API Versioning

As of June 2026, Airtop's API uses `/v1/` but versioning practices are unclear. Breaking changes risk is unknown.

---

## Recommendations

### When Airtop Is the Right Choice

- **Business users** who need no-code browser automation
- **GTM teams** with pre-built template needs (lead enrichment, company research)
- **SOC2/HIPAA compliance** requirements (enterprise regulated industries)
- **Scheduled recurring monitoring** (pricing checks, competitor tracking)
- **Simple, non-anti-bot tasks** where cost matters more than extract quality

### When to Use Something Else

- **Meta/Facebook/Cloudflare scraping** → Browser Use Cloud (proven anti-detection)
- **AI agent integration with MCP** → Browser Use Cloud
- **Programmatic control with SDK** → Browser Use Cloud (Python SDK v3)
- **Structured typed output** → Browser Use Cloud (Pydantic/Zod)
- **Maximum flexibility/custom proxies** → Playwright + Decodo on raw infra
- **Authenticated sessions** → Browser Use Cloud (persistent profiles)
