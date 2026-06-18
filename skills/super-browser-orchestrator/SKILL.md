---
name: super-browser-orchestrator
description: Orchestrate universal browser and computer automation with Super Saiyan Browser. Use when an agent needs to receive a browser/computer task, classify risk, call the planner and provider specialists, request exact missing setup, dispatch execution through the Super Saiyan Browser CLI or MCP server, and verify the final result.
---

# Super Saiyan Browser Orchestrator

## Role

Own the whole job. Convert the user request into a safe, executable browser/computer automation workflow.

## Workflow

0. On first use or when setup is unclear, call `super-browser setup` or MCP `setup_walkthrough`, then `env_checklist` / `browser_doctor`. Use signup URLs from the walkthrough; never ask the user to paste secrets in chat.
1. Classify the task as read-only, mutating, external write, credential-bearing, destructive, long-running, authenticated, anti-bot, desktop, or raw HTTP.
2. Call `super-browser plan --goal "<goal>"` or the `plan_browser_task` MCP tool.
3. Read `council_report`; review all `review_loops` (3 direct / 5 council), `deliberation_complete`, `execution_pattern`, and `documented_recommendations` before execution.
4. Report exact missing env vars or provider setup; never ask for secrets in chat.
5. Use `install_super_browser_skill` or `init_super_browser_mcp` when an MCP-only setup flow needs the bundle or config generated.
6. Dispatch execution only after `deliberation_complete` is true and approval requirements are satisfied.
7. Call `super-browser verify <run-id>` or `verify_browser_run`.
8. Inspect `policy_guard`, `plan_integrity`, `approval_integrity`, and run-report final-provider/attempt consistency before retrying, handing off, or making final claims.
9. Return the final run report with provider, cost notes, artifacts, failures, policy guard, and confidence.

## Defaults

- Prefer the cheapest reliable tool, not the cheapest tool blindly.
- Use local Playwright for simple deterministic work.
- Use Browser Use for high-risk anti-bot workflows and logged-in cloud profiles.
- Use Hyperbrowser or Airtop for general cloud browser work at scale.
- Use Browserbase only when Stagehand/hosted-agent/BYOK is explicitly required (docs-only until adapter ships).
- Use Steel for hosted Chromium sessions over Playwright CDP.
- Use Orgo for full desktop/computer use.
- Use Decodo/raw HTTP for API endpoints and cheap fetches.
- Require approval for posting, commenting, messaging, submitting, uploading, payments, trading, banking, payouts, legal, government, health, insurance, identity, account changes, credentials, secrets, infrastructure, billing, workspace/channel/role/moderation changes, or destructive actions.

## References

- Read `../../references/routing-playbook.md` for provider routing.
- Read `../../references/combo-playbook.md` and `../../references/providers/README.md` for strategic provider use.
- Read `../../references/security-and-approval-policy.md` before external writes.
- Read `../../references/live-test-matrix.md` before claiming a provider is working.
