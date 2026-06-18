---
name: playwright-specialist
description: "Escalation rank 1 (default local browser): Use local Playwright for deterministic browser automation." Use when a task is simple, testable, selector-driven, local, cheap, or needs fixture-based verification without advanced anti-bot or personal auth-session requirements.
---

# Playwright Specialist

## Use For

- Local deterministic browser control.
- Web app testing and visual verification.
- Known selectors, form filling, screenshots, downloads, and fixture sites.
- Cheap first pass before cloud providers.

## Do Not Use For

- Advanced anti-bot sites such as Meta, LinkedIn, Cloudflare-heavy pages, PerimeterX, or DataDome.
- Logged-in personal Chrome sessions unless the profile strategy is explicit.
- Tasks where natural-language exploration is more important than deterministic control.

## Setup

```bash
python3 -m pip install "super-browser[playwright]"
python3 -m playwright install chromium
```

## Verification

Use screenshots, traces, DOM snapshots, and fixture tests. Prefer Playwright for the Super Saiyan Browser test suite even when production uses a cloud provider.

`super-browser doctor` must report `ready_local` with `browser_runtime_available=true` before treating local Playwright as production-ready. If it reports `runtime_missing`, the Python package exists but Chromium cannot launch; run `python3 -m playwright install chromium` and rerun doctor plus the local live test.

Super Saiyan Browser installs a browser request target-scope guard before navigation. A page that redirects or loads subresources into `loopback`, `private_network`, `link_local`, or `local_file` from a different planned scope is blocked with a `browser_request_target_scope` event and metadata instead of page artifacts. Public-looking hostnames are resolved before the request is allowed, so DNS results that point at loopback, private-network, or link-local are blocked too. Treat target-scope and DNS blocks as non-resumable safety stops; create a new run or replan for the intended target scope.
