# Deep-Dive: Anchor Browser & Orgo Machines

> **Evaluated:** Anchor Browser (`anchorbrowser.io`) and Orgo Machines (`orgo.ai`)  
> **Outcome:** Anchor skipped; Orgo added as Tier 5  
> **Last Updated:** June 5, 2026

---

## Table of Contents

1. [Anchor Browser](#anchor-browser)
   - [Platform Overview](#platform-overview)
   - [Architecture](#architecture)
   - [Web-Bot-Auth Technology](#web-bot-auth-technology)
   - [Pricing Opacity](#pricing-opacity)
   - [Why Anchor Was Skipped](#why-anchor-was-skipped)
2. [Orgo Machines](#orgo-machines)
   - [Platform Overview](#platform-overview-1)
   - [Architecture: Firecracker Micro-VMs](#architecture-firecracker-micro-vms)
   - [Sub-500ms Boot Time](#sub-500ms-boot-time)
   - [Pricing Tiers](#pricing-tiers)
   - [Why Orgo Was Added](#why-orgo-was-added)
3. [Comparison Matrix](#comparison-matrix)

---

## Anchor Browser

### Platform Overview

Anchor Browser is an **enterprise cloud browser** platform. It provides remote browser instances with infrastructure-level anti-detection, session persistence, and identity management via its "Web-Bot-Auth" system.

**Website:** `anchorbrowser.io`

### Architecture

Anchor runs cloud-hosted Chromium instances with:

- **Infrastructure-level anti-detection** — patches at the browser binary level (similar to Browser Use Cloud)
- **Persistent browser profiles** — sessions maintain cookies, localStorage, and browser state across multiple automation runs
- **Proxy rotation built-in** — residential and datacenter proxy pools
- **Enterprise-grade isolation** — each session runs in an isolated container
- **Centralized identity management** — Web-Bot-Auth for managing authenticated sessions at scale

### Web-Bot-Auth Technology

Anchor's headline feature is "Web-Bot-Auth" — an identity layer that:

1. **Manages authenticated browser profiles at scale** — you upload login credentials once, Anchor maintains the session
2. **Handles 2FA automatically** — Anchor's automation resolves MFA challenges (email, TOTP, SMS)
3. **Rotates sessions** — prevents detection by not reusing the same session indefinitely
4. **Shares sessions across automation runs** — multiple tasks can reuse the same authenticated profile

This is conceptually similar to Browser Use Cloud's **Profiles + Agent Mail** feature, but Anchor positions it as a managed service rather than a self-serve capability.

### Pricing Opacity

Anchor's pricing is a **deliberate black box:**

| Aspect | Detail |
|--------|--------|
| **Public pricing page** | ❌ Does not exist |
| **Free tier** | ❌ None |
| **Trial** | ❌ No self-serve signup |
| **Sales process** | Enterprise-only, schedule a call |
| **Price range** | Unknown — estimated $500-5,000+/mo based on industry comparables |
| **Minimum commitment** | Annual contract typical |

This opacity was a significant factor in Anchor being skipped. For a project where budget predictability matters, an opaque enterprise sales process is a blocker.

### Why Anchor Was Skipped

| Reason | Detail |
|--------|--------|
| **Pricing opacity** | No public pricing. Enterprise sales call required. Cannot budget without committing. |
| **Overlap with Browser Use Cloud** | Both are cloud browsers with hardened Chromium, persistent profiles, and 2FA handling. Anchor's Web-Bot-Auth is a different packaging of features Browser Use Cloud already has. |
| **No MCP support** | Anchor has no documented MCP server integration. Browser Use Cloud has a first-class MCP endpoint. |
| **No public SDK** | No Python/Node.js SDK documented. Browser Use Cloud has SDK v3 with Pydantic support. |
| **No self-serve onboarding** | Can't sign up and try it. Can't verify anti-detection claims independently. |
| **Enterprise lock-in** | Proprietary everything. No open-source core. No escape hatch. |
| **Value-add unclear** | At $29/mo, Browser Use Cloud provides the same core capabilities (anti-detection, profiles, proxies, 2FA). Anchor would need to justify a 10-100× price premium. |

**Verdict:** If Anchor offered something Browser Use Cloud doesn't (and published its pricing), it might earn a place. As of June 2026, it doesn't justify the enterprise negotiation overhead.

---

## Orgo Machines

### Platform Overview

Orgo Machines is a **cloud VM platform purpose-built for AI agents.** Unlike cloud browsers (which give you a browser tab), Orgo gives you a full Linux desktop — with a file system, terminal, application launcher, and multi-window support.

**Website:** `orgo.ai`  
**Open-source clone:** `github.com/Julianb233/orgo-clone`

### Value Proposition

Orgo fills a gap that browser-only tools cannot:

| Browser Tools CAN Do | Orgo Machines CAN Do (that browsers can't) |
|----------------------|-------------------------------------------|
| Navigate web pages | Install and run desktop applications |
| Click DOM elements | Multi-window workflows |
| Extract page data | Local file processing (convert, compile, analyze) |
| Fill web forms | Full terminal/shell access |
| Take page screenshots | Run Playwright/Selenium locally inside VM |
| Execute page JavaScript | GPU-accelerated workloads (roadmap) |
| | System-level automation (xdotool, cron, services) |
| | Persistent development environments |

### Architecture: Firecracker Micro-VMs

Orgo uses **AWS Firecracker** micro-VMs — the same virtualization technology that powers AWS Lambda and Fargate. Key properties:

| Property | Detail |
|----------|--------|
| **Isolation** | Hardware-level (KVM-based), not container-level |
| **Boot time** | Sub-500ms cold start (industry-leading) |
| **Security** | Double encryption at rest, rotating credentials per session |
| **Resource limits** | vCPU, RAM, and disk configurable per plan |
| **OS** | Ubuntu-based Linux with Xfce or Fluxbox desktop |
| **Ephemeral** | VMs are disposable; terminate after use |

```
┌──────────────────────────────────────┐
│            ORGO MACHINE              │
│  ┌────────────────────────────────┐  │
│  │      Linux Desktop (Xfce)      │  │
│  │  ┌──────────┐  ┌────────────┐  │  │
│  │  │Playwright│  │  Terminal  │  │  │
│  │  │ Chrome   │  │    Shell   │  │  │
│  │  └──────────┘  └────────────┘  │  │
│  │  ┌──────────┐  ┌────────────┐  │  │
│  │  │ VS Code  │  │  File Mgr  │  │  │
│  │  └──────────┘  └────────────┘  │  │
│  └────────────────────────────────┘  │
│         Firecracker micro-VM          │
└──────────────────────────────────────┘
```

### Sub-500ms Boot Time

Orgo's most impressive technical feature is its cold boot time:

| Platform | Cold Boot | Warm Boot |
|----------|-----------|-----------|
| **Orgo Machines** | < 500ms | < 100ms |
| AWS EC2 | 30-60s | N/A |
| Docker container | 1-5s | < 100ms |
| GitHub Codespaces | 30-120s | 5-15s |

This enables **instant agent dispatch** — you can spin up a VM, execute a task, and tear it down in seconds, making it cost-effective for on-demand automation.

### API & Integration

Orgo provides a REST API + WebSocket for real-time control:

#### REST API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/machines` | POST | Create a new VM |
| `/v1/machines/{id}` | GET | Get VM status and connection info |
| `/v1/machines/{id}` | DELETE | Terminate VM |
| `/v1/machines/{id}/execute` | POST | Upload and execute a script |
| `/v1/machines/{id}/files` | GET | Download a file from the VM |
| `/v1/machines/{id}/screenshot` | GET | Capture desktop screenshot |
| `/v1/machines/{id}/ws` | WebSocket | Real-time desktop control |

#### Python SDK

```python
from orgo import OrgoClient

client = OrgoClient(api_key="org_...")
vm = client.vms.create(template="ubuntu-desktop")
vm.wait_ready()
vm.execute("playwright test --headed")
vm.screenshot()
vm.terminate()
```

#### WebSocket API (Real-time Control)

```python
async with websockets.connect("wss://api.orgo.ai/v1/vms/{vm_id}/ws") as ws:
    await ws.send(json.dumps({"type": "click", "x": 500, "y": 300}))
    await ws.send(json.dumps({"type": "type", "text": "hello world"}))
    async for msg in ws:
        frame = json.loads(msg)
        # frame["screenshot"] = base64 PNG
```

### Pricing Tiers

| Plan | Price/mo | VMs | vCPU | RAM | Disk | AI Credits |
|------|----------|-----|------|-----|------|------------|
| **Hacker** | $29 | 5 | 1 | 4GB | 20GB | $10 |
| **Team** | $112 | 20 | 2 | 8GB | 30GB | $50 |
| **Scale** | $224 | 50 | 4 | 16GB | 50GB | $100 |

**Annual billing:** ~10% discount.

**AI Credits:** Included credits for using Orgo's built-in Claude Computer Use, OpenAI CUA, or other models. You can also use your own LLM keys (BYOK).

**Cost per VM:** At the Hacker tier, 5 VMs for $29/mo = ~$5.80/VM/month. This is cost-effective compared to maintaining your own VM infrastructure.

### Key Features

| Feature | Detail |
|---------|--------|
| **Templates** | Pre-built Docker images; custom templates supported |
| **File transfer** | Upload scripts, download results (screenshots, CSVs, PDFs) |
| **Desktop environment** | Xfce/Fluxbox with xdotool for mouse/keyboard |
| **Multi-model** | Claude Computer Use, OpenAI CUA, OpenClaw, LangChain |
| **Encryption** | Double encryption at rest; rotating credentials per session |
| **GPU roadmap** | NVIDIA MIG on A100, whole-GPU A10/L40s (not yet GA) |
| **Open-source clone** | `github.com/Julianb233/orgo-clone` (Fastify/PostgreSQL/Dockerode/BullMQ) |

### Why Orgo Was Added

Orgo was added as **Tier 5** in response to a second request from Justin. The rationale:

1. **Browser tools can't do everything** — Installing desktop apps, multi-window workflows, file system operations, and terminal access are all outside browser tool scope.

2. **Maximum stealth option** — Orgo VMs run on residential/ISP IPs. Because you control the full OS, you can install Playwright + patches + any custom anti-detection you need. This is the "nuclear option" for sites where even Browser Use Cloud struggles.

3. **Complements the stack** — Orgo doesn't overlap with tiers 1-4. It extends the stack into a new dimension (desktop automation) rather than competing for the same use cases.

4. **Sub-500ms boot enables on-demand usage** — Unlike traditional VMs that take 30-60 seconds to provision, Orgo's speed makes it viable for programmatic, on-demand automation.

5. **Open-source clone exists** — The `orgo-clone` provides a self-hosting escape hatch, reducing vendor lock-in risk.

### Limitations

| Limitation | Detail |
|------------|--------|
| **Cost per VM** | 5 concurrent VMs on Hacker plan; scaling up gets expensive |
| **No built-in anti-detection** | Raw Linux desktop — you bring your own stealth (Playwright patches, browser hardening) |
| **Not a browser tool** | You install and configure Playwright/Puppeteer yourself |
| **GPU not yet available** | Roadmap, not production (as of June 2026) |
| **Proprietary** | Closed-source SaaS (use the clone for self-hosting) |
| **No MCP** | REST API only; no MCP server endpoint |
| **Learning curve** | Requires understanding VM management, not just browser APIs |

---

## Comparison Matrix

### Anchor vs. Orgo vs. Browser Use Cloud

| Dimension | Anchor Browser | Orgo Machines | Browser Use Cloud |
|-----------|:---:|:---:|:---:|
| **What you get** | Cloud browser tab | Full Linux desktop VM | Cloud browser tab |
| **Anti-detection** | ✅ Built-in | ❌ DIY (you install) | ✅ Built-in hardened Chromium |
| **Pricing** | ❌ Opaque/enterprise | ✅ $29/mo (5 VMs) | ✅ $29/mo (Dev) |
| **Self-serve signup** | ❌ Sales call | ✅ Instant | ✅ Instant |
| **MCP support** | ❌ | ❌ | ✅ |
| **SDK** | ❌ Undocumented | ✅ Python + WebSocket | ✅ Python SDK v3 + CDP |
| **Desktop automation** | ❌ Browser only | ✅ Full OS | ❌ Browser only |
| **Open-source option** | ❌ | ✅ Clone available | ❌ (cloud features) |
| **2FA handling** | ✅ Web-Bot-Auth | ❌ Manual | ✅ Agent Mail |
| **Session persistence** | ✅ Profiles | ✅ VM persistence | ✅ Profiles |
| **Boot time** | N/A (always-on) | < 500ms | ~5-15s session start |
| **Best for** | Enterprise identity mgmt | Full desktop automation | AI agent browsing |

### Decision: Tier Placement

> **Superseded (June 2026):** the ladder below was the original research ranking. Browserbase and Rtrvr were later cut for simplicity. Current ladder: Playwright + Browser Use (T1) → Hyperbrowser + Airtop (T2) → Steel (T3) → Orgo (T4), Decodo as a separate raw-HTTP lane.

| Tool | Tier | Reason |
|------|:----:|--------|
| **Browser Use Cloud** | 1 | Best anti-detection, MCP-native, AI-native |
| **Browserbase** | 2 (removed) | Originally: already in Hermes, free, quick single-page |
| **Airtop** | 3 | No-code, scheduled, SOC2, backup |
| **Rtrvr** | 4 (removed) | Originally: MCP-native, auth sessions, BYOK |
| **Orgo Machines** | 5 | Full desktop VMs, extends beyond browsers |
| **Anchor** | Skipped | Pricing opacity, overlap with Tier 1, no self-serve |
| **Decodo** | Proxy layer | $2/GB residential IPs for raw HTTP scraping |
