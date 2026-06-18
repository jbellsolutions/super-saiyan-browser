---
name: super-browser
description: Universal browser and computer automation skill. Use when an agent needs to plan, route, execute, or verify browser automation, authenticated browsing, anti-bot workflows, raw HTTP/proxy fetching, or full desktop computer-use tasks with Super Saiyan Browser.
---

# Super Saiyan Browser

Super Saiyan Browser is now plugin-first. Prefer the role skills in `skills/` and the CLI/MCP runtime.

## Onboarding (first message)

When a user drops this repo link, say:

> Hey, here's how this works. Super Saiyan Browser routes browser and computer tasks to the right backend (Playwright, Browser Use, Hyperbrowser, Steel, Orgo, raw HTTP). Run `./scripts/super-browser setup` or MCP `setup_walkthrough` and follow every step. Plan before run; wait for `deliberation_complete`; stop for approval on external writes; verify before claiming success.

## Quick Start

```bash
./scripts/super-browser doctor
./scripts/super-browser providers
./scripts/super-browser install-skill --target ~/.codex/skills
./scripts/super-browser init-mcp
./scripts/super-browser plan --goal "Extract product names from https://example.com"
./scripts/super-browser plan --goal "Extract public data" --allow-provider playwright --max-cost-usd 0
./scripts/super-browser run --goal "Fetch a slow endpoint" --url "https://example.com/data.json" --timeout-seconds 60
./scripts/super-browser run --goal "Draft a LinkedIn comment but do not publish"
./scripts/super-browser approve <run-id> --by human --reason "approved exact action"
./scripts/super-browser get <run-id>
./scripts/super-browser handoff <run-id>
./scripts/super-browser runs --status awaiting_approval --limit 20
./scripts/super-browser resume <run-id>
./scripts/super-browser verify <run-id>
./scripts/super-browser live-test --provider local
./scripts/verify-super-browser
```

CLI commands return JSON on success and redacted stderr JSON with `error` and `error_type` for known Super Saiyan Browser command failures. MCP tools advertise `inputSchema` plus read-only/execution annotations, validate required fields, provider enums, cost ceilings, timeout ceilings, booleans, setup paths, non-blank string fields, and unknown arguments before execution, and return `structuredContent` for direct JSON consumption. Use `install_super_browser_skill` and `init_super_browser_mcp` when an MCP-only agent needs to install the bundle or generate config without shelling out to the CLI.

Treat `approve_browser_run` and `resume_browser_run` as execution-capable tools. Their MCP annotations are conservative because `approve_browser_run` with `execute=true` and `resume_browser_run` after approval can dispatch a provider action.

Recoverable tool errors and unexpected exceptions inside known tools return `isError: true` with redacted structured error details and `error_type`; unknown tools, unsupported protocol methods, malformed `resources/read` envelopes, malformed JSON, and non-object JSON-RPC requests remain protocol errors. Well-formed JSON-RPC notifications without an `id`, including `notifications/initialized`, are consumed without a response. Malformed or non-object requests return a `null` id and must not reuse an earlier request id.

Use MCP `resources/list` and `resources/read` to load read-only provider docs and playbooks when the agent does not have filesystem access. Stable resource URIs include `super-browser://references/provider-matrix`, `super-browser://references/routing-playbook`, and `super-browser://skills/<skill-name>`. Resource docs are exposed only from a verified Super Saiyan Browser repository, installed bundle root, or packaged `share/super-browser` asset tree, never from an arbitrary MCP current working directory.

Use `./scripts/super-browser install-skill --target <skill-root>` to copy a self-contained Super Saiyan Browser bundle for another agent; add `--force` to replace an older bundle cleanly. Installed bundles exclude local-only secrets, state, caches, dependency folders, logs, sqlite files, symlinks, and build output. Use `./scripts/super-browser init-mcp --path <config.json> --merge` to add Super Saiyan Browser to an existing MCP config without dropping other servers. `init-mcp --cwd` must point to this repo or an installed bundle with an executable `mcp/super-browser-server`; invalid paths fail before config files are written. If `super-browser` is running from a normal Python package install, `init-mcp` emits a module-based MCP command and points `SUPER_BROWSER_REPO_ROOT` to the packaged `share/super-browser` asset tree so `install-skill` and MCP markdown resources still work. If a broken/minimal package omits the asset tree, `install-skill` reports `source_unavailable`.

Use `./scripts/verify-super-browser` as the default full local verification entrypoint before claiming the repo or a change is ready. It uses temporary `SUPER_BROWSER_STATE_DIR` and `PYTHONPYCACHEPREFIX` values by default so verification does not leave `.super-browser` or `__pycache__` files in the repo. Set `SUPER_BROWSER_VERIFY_TMP_DIR`, `SUPER_BROWSER_VERIFY_STATE_DIR`, or `SUPER_BROWSER_VERIFY_PYCACHE_DIR` only when debugging and you need to keep those artifacts.

## Agent Roles

- `super-browser-orchestrator`: Owns the workflow end to end.
- `super-browser-planner`: Chooses providers and builds the execution plan.
- Provider specialists: Give tool-specific setup, limits, and verification guidance.
- `publishing-safety-specialist`: Gates external writes.
- `super-browser-verifier`: Checks traces, artifacts, and confidence.

## Approval Lifecycle

External writes and credential-bearing tasks create a run in `awaiting_approval`.

External writes include posting, commenting, replying/responding, sending email, sending messages/DMs, submitting non-search/state-changing forms, uploading, liking/reacting/upvoting/downvoting, quote/repost/share-to-story actions, starring/watching/forking repos, bookmarking/saving/pinning/favoriting platform content, following/connecting, joining/creating groups, creating events/pages, accepting/declining/removing/canceling/confirming requests, invites, or connections, removing followers/friends/members, RSVPs, event attendance/check-ins/interested/going marks, reporting/blocking/muting, notification toggles, message/email archive/read-state changes, tagging/mentioning people, booking/scheduling/reserving, requesting info/demo/quotes/pricing, applying, subscribing, reviews, poll votes, CRM lead/contact/customer create/assign/enroll/stage/list updates, project/repository issue, ticket, task, card, pull-request, and repo changes, cloud file/folder/document creation, renames, moves, copies, sharing/access/permission/public-visibility changes, app/integration install/authorize/connect changes, settings/preference saves, API-key/token creation, rotation, or revocation, secret reveal/copy requests, webhook creation or updates, deployment creation, promotion, rollback, or redeploys, DNS record/nameserver changes, environment-variable changes, billing trial/plan/payment-method changes, trading orders, asset sales, swaps, staking, unstaking, position opens/closes/liquidations, withdrawals, deposits, fund transfers, ACH/wire/bank transfers, bank/wallet/brokerage/payout account changes, legal signatures/certifications/attestations, tax and court filings, insurance claim/policy changes, benefits or health-plan enrollment changes, prescription refills, medical form/record delivery, passport/visa/government-ID actions, voter registration, regulated address changes, emergency contact changes, workspace/channel/server/community/page creation, rename, archive, or unarchive changes, member additions, kicks, bans, unbans, role changes, thread/comment locks, ad creation/boosting/promotion, cart/basket/bag/wishlist/waitlist additions, removals, or quantity changes, checkout address changes, promo/coupon/offer actions, order placement/cancellation/returns/refunds/payments, purchases/bids/donations/checkouts, profile/account changes, destructive account actions, and clicking/tapping/pressing/selecting/activating final write buttons or controls.

Treat undo/removal wording as external write wording too: unlike, unreact, unbookmark, unsave, unfavorite, unstar, stop watching, trash/restore cloud files, cancel/reschedule calendar events, cancel scheduled posts/messages/emails, remove CRM records from campaigns or sequences, and unenroll contacts.

Draft-only text preparation does not require approval when the request explicitly says not to publish, post, comment, reply, respond, message/DM, send, or submit. The provider prompt must derive the draft-only boundary from current policy classification, not only from mutable stored plan flags, and must still tell the browser agent to stop before any final publish, post, comment, reply, respond, message/DM, send, submit, upload, follow, connect, react, share, CRM/cart/order/payment/trading/banking/payout/legal/government/health/insurance/identity/project/repository/cloud-file/sharing/integration/settings/secrets/infrastructure/billing/workspace/channel/role/moderation/notification/message-state/member/account change, click, tap, press, select, or activate control. Hyphenated content terms such as "follow-up" do not count as the platform action "follow" unless the request actually asks for a follow/following action. Business/content phrases such as "lead magnet," "invite template," "posting schedule," "apply a filter," "book notes," or "review summary" stay non-external unless the request also asks for a real site/account state change. Public documentation, help articles, guides, policy pages, best-practice pages, examples, and local notes about sharing, OAuth, tokens, auth, integrations, API keys, webhooks, DNS records, environment variables, billing, trading, banking, ACH/wire transfers, payouts, legal forms, tax filing, insurance claims, prescriptions, medical records, passports, visas, government IDs, channels, workspaces, roles, or moderation stay read-only when the full request stays reference-only. Creating local lead/contact/prospect/customer lists, CSVs, JSON files, or run artifacts from extracted data is local output, not an external write; writing or syncing those records into CRM, Salesforce, HubSpot, Pipedrive, Zoho, Apollo, campaigns, sequences, or pipelines remains approval-gated. File uploads, credential-bearing work, and ambiguous "draft and post" or "write and send" requests still require approval.

Read-only scanning of visible public posts, comments, forum messages, and group content is allowed as a read task only when the full request stays read-only. Reading personal inboxes, DMs, or private messages is credential-bearing and requires approval. A browse/read/search/list prefix does not neutralize a later write: scanning plus posting, commenting, replying, responding, sending, liking, following, connecting, submitting, CRM updates, cart/order/payment/trading/banking/payout changes, legal/government/health/insurance/identity changes, project/repository updates, cloud-file/sharing/integration/settings changes, secret/API-key changes, webhook/deployment/DNS/environment-variable changes, billing/payment-method changes, workspace/channel/role/moderation changes, thread locks, notification toggles, archive/read-state changes, member removals, or pressing final write controls remains approval-gated.

Submitting public search, filter, or sort forms only to fetch visible public results is read-only when the query does not include credentials, private/personal data, or another external action. Public documentation, help articles, guides, policy pages, best-practice pages, examples, and local notes about sharing, OAuth, tokens, auth, integrations, API keys, webhooks, DNS records, environment variables, billing, trading, banking, ACH/wire transfers, payouts, legal forms, tax filing, insurance claims, prescriptions, medical records, passports, visas, government IDs, channels, workspaces, roles, or moderation are also read-only when the full request stays reference-only. These exceptions do not cover a later like, save, bookmark, share, follow, connect, CRM update, cart/order/payment/trading/banking/payout change, legal/government/health/insurance/identity change, project/repository update, cloud-file/sharing/integration/settings change, secret/API-key change, webhook/deployment/DNS/environment-variable change, billing/payment-method change, workspace/channel/role/moderation change, notification toggle, message/email state change, or other external write in the same request. Lead, contact, application, checkout, signup, comment, message, quote, demo, pricing, upload, payment, registration, review, poll, booking, appointment, reservation, subscribe, and unsubscribe forms remain approval-gated.

Local delivery wording such as "send me a summary" or "send us the report" is read-only only when it is not combined with an external action. A mixed request like "send me the findings, then post a comment" or "send me a summary and email this lead" is still an external write and stops for approval.

Use `super-browser approve <run-id> --by <actor> --reason <audit-note>` or `approve_browser_run` with `by` and `reason` to record approval. The actor and reason are required for auditability. Approval does not execute by default; pass `--execute` or MCP `execute=true` only when the exact external action is approved.

Use `super-browser deny <run-id> --by <actor> --reason <audit-note>` or `deny_browser_run` with `by` and `reason` to record denial and prove the write was stopped.

The low-level `execute_plan()` adapter path is guarded too. It re-checks task policy and blocks approval-gated plans unless the durable runtime passes structured `approval_context` after approval is recorded. A bare approval boolean is not enough.

Approval requests include an approval id, required approval stage, action fingerprint, and plan fingerprint. `approve` must reject the pending approval if the id/stage is missing or either fingerprint no longer matches the current run plan, and execution must compare the current plan to the fingerprint stored on the approved record. If approved decision metadata is missing or an approved external-write attempt has already started, `resume` must stop before provider dispatch; retries must create a fresh `provider_retry` approval request instead of retrying the post/comment/message/form submission. Retry protection must derive write risk from policy classification as well as stored flags, so stale or hand-built run records cannot skip duplicate-write protection by setting `task.external_write=false`.

Approved runs expire before provider execution after the approval freshness window. The default is 30 minutes and can be tuned with `SUPER_BROWSER_APPROVAL_TTL_SECONDS`. When an approved run is resumed after expiry, resume is safe only as a state transition: it must record `approval_expired`, return to `awaiting_approval`, create a fresh pending approval for the same stage, and set handoff `resume.will_execute_provider=false`.

Provider prompts must include the current policy boundary for read-only, authenticated read/navigation, draft-only, and external-write runs. Prompt safety is a provider-control layer only; never treat it as a substitute for durable approval records, adapter/runtime guards, target-scope checks, duplicate-write retry protection, verifier policy guards, or handoff approval-integrity checks.

## Resume Lifecycle

Use `super-browser get <run-id>` or `get_browser_run` for read-only lookup. Use `super-browser handoff <run-id>` or `handoff_browser_run` when another agent needs the compact run summary, route, provider readiness, approval state, verifier summary, commands, docs, and next steps. Handoff safety and durability fields such as `task.external_write`, `task.requires_auth`, `task.draft_only`, `task.long_running`, `route.approval_required`, and `approval.required` must come from verifier `policy_guard`, not only from mutable stored plan flags. Use `super-browser runs --status <status> --limit 20` or `list_browser_runs` when an agent needs to discover saved runs after compaction, handoff, or a lost run id. Run lists are compact summaries by default; use CLI `--details` or MCP `include_details=true` only when the full payload list is needed. Empty lookup/list calls do not create `.super-browser` state. If a stored run payload cannot be decoded, lookup/list should surface `store_payload_corrupt` as a low-confidence failed record; resume must block before provider dispatch, and the agent should create a new run instead of treating it as a provider failure.

Use `super-browser resume <run-id>` or `resume_browser_run` to continue a planned, approved, blocked, failed, or stale executing run. Resume does not bypass approval; `awaiting_approval` runs stay stopped until approval is recorded. Execution is atomically claimed so concurrent agents do not start the same run twice. Active executing runs stay no-ops while their execution lease is valid, and the no-op is saved without clearing the lease. Expired leases are recovered and resumed. Lease duration derives long-running status from current policy classification as well as the stored flag, so stale `task.long_running=false` cannot shorten a monitor/overnight/recurring run's duplicate-worker guard. Terminal provider results clear the lease. In handoff output, inspect both `resume.safe_to_resume` and `resume.will_execute_provider`; after a failed approved external write, resume is safe because it creates a fresh retry approval, but it must not start another provider attempt until that retry approval is approved. If `plan_integrity` is `mismatch` or `missing`, verifier failures include `missing_run_report`, `missing_artifact_path`, `artifact_hash_mismatch`, `status_mismatch`, impossible final-provider/attempt evidence, provider sequence constraints fail, `approval_integrity` is `missing`, `mismatch`, `missing_fingerprint`, `missing_approval_id`, `missing_required_before`, `invalid_required_before`, `missing_decision_metadata`, or `unknown_status`, or `policy_guard.non_resumable_safety_stop=true`, handoff must mark resume unsafe and direct resume must stop with `resume_blocked` before any execution claim. `missing_run_report` is allowed only for stale execution recovery before the recovered attempt runs, or for the non-executing transition that creates a fresh external-write retry approval.

## Routing Defaults

Routing is capability-first: the router asks "what does the task need?" and filters providers by capability (auth, anti-bot, CAPTCHA, profiles, proxy injection, fleet, desktop, raw HTTP). An escalation rank then orders equally capable providers from cheapest/most deterministic to most expensive — it is a cost tie-breaker, not the routing model. See `references/routing-playbook.md` for the capability table.

- Simple deterministic tasks → local Playwright (free, rank 1).
- Anti-bot, CAPTCHA, hard browser tasks, and logged-in cloud profiles → Browser Use (rank 1 cloud).
- Cloud-scale scraping, page-query, session workflows, profiles, proxy injection, fleets → Hyperbrowser and Airtop (rank 2).
- Hosted Chromium sessions over Playwright CDP, profiles, proxy injection, fleets → Steel (rank 3).
- Full desktop/computer use → Orgo (rank 4, the only desktop-capable provider).
- Raw HTTP and cheap residential proxy fetches with a concrete `http://`/`https://` endpoint → Decodo, a separate lane that never enters browser fallbacks.
- Hyperbrowser, Steel, and Airtop stay evaluating providers until live tests pass for a task class.

## Council Reports

Every `super-browser plan` result includes `council_report`. Use it to inspect provider specialist recommendations, required setup, missing env vars, review loops, approval gates, and the selected provider sequence before execution.

Use `--allow-provider` for strict provider allowlists, `--max-cost-usd` for cost-floor routing, and `--timeout-seconds` for a provider execution ceiling. Planning fails if no provider satisfies the constraints. The planner avoids URL-required providers when no starting URL is available. Raw HTTP/API tasks require a concrete `http://` or `https://` starting URL; if the endpoint is missing, planning fails instead of silently switching to a browser provider. URLs embedded in prose or Markdown goals have common trailing delimiters stripped, including `>`, `]`, quotes, and sentence punctuation; explicit URLs with raw whitespace are rejected and should use percent encoding. Runtime execution, verifier, handoff, and direct resume re-check task payload validity, URL-derived target scope, provider allowlists, file-URL provider restrictions, unknown providers, URL-required primary providers without a starting URL, raw HTTP without an HTTP endpoint, and max-cost ceilings before provider dispatch, so a stale or hand-built plan cannot smuggle malformed constraints, downgrade a sensitive target scope, or widen the selected sequence. Inspect `cost_estimate`, `task.timeout_seconds`, and `council_report.planner_decision.timeout_seconds` before execution.

Use `super-browser doctor` or `browser_doctor` before live/provider execution. Treat `usable_now` as "can be attempted." Treat `production_ready` as scoped, not blanket certification: inspect `production_ready_scope`, `certified_workflow_classes`, `uncertified_workflow_classes`, `production_blockers`, and `latest_live_test.workflow_class` before relying on a provider for a task class. `live_test_passed` means fresh persisted evidence exists for the listed workflow class or classes; it does not prove social posting, authenticated, anti-bot, or desktop workflows unless that class is listed. `runtime_missing` means a local package exists but the browser runtime cannot launch; for Playwright, run `playwright install chromium` before claiming local readiness. `requires_live_test_before_production=true` means setup exists but production proof is missing. `requires_live_test_before_broader_production=true` means one class is proven but another supported class is still unproven. `live_test_stale` means rerun the provider live test. `decodo-http` can be `usable_direct_http_no_proxy`, which means direct raw HTTP can run for a supplied HTTP endpoint but residential proxy routing still needs `DECODO_PROXY`.

Use `super-browser production-readiness` or MCP `production_readiness` as the final go-live gate. It returns `production_ready=false` and CLI exit `1` when required providers are missing env vars, have stale or missing live evidence, or still have uncertified workflow classes. Do not claim production readiness when this gate is blocked.

Use `super-browser setup` or MCP `setup_walkthrough` on first install — step-by-step clone, pip, skills, MCP, doctor, and API signup links. Use `super-browser env-checklist` or MCP `env_checklist` before setup handoff or paid/provider execution. It reports required and optional env var names, configured/missing status, provider mapping, global runtime knobs such as `SUPER_BROWSER_APPROVAL_TTL_SECONDS`, and live-test commands without exposing secret values.

Use `super-browser bundle-manifest` or MCP `bundle_manifest` before handing Super Saiyan Browser to another agent, auditing an installed bundle, or preparing a release. The manifest is the authoritative hashed inventory of bundle files, entrypoints, specialist skills, providers, MCP tools, and docs resources. Installed bundles include `super-browser-manifest.json`.

Optional provider transport overrides such as `ORGO_API_BASE`, `AIRTOP_API_BASE`, `HYPERBROWSER_API_BASE`, and `STEEL_CDP_URL` are inspected before credentials are sent. Loopback self-hosted providers may use HTTP/WS. Private-network or link-local provider endpoints require `SUPER_BROWSER_ALLOW_INTERNAL_PROVIDER_BASES=1`, and insecure non-loopback HTTP/WS requires `SUPER_BROWSER_ALLOW_INSECURE_PROVIDER_BASES=1`.

Request a specific class with `super-browser live-test --provider <provider> --workflow-class <class>` or MCP `run_browser_live_tests` with `workflow_class`. Supported classes accumulate per provider, so a later `external_write_gate` proof does not erase an earlier `raw_http_direct`, `general_read`, `authenticated_read`, or `desktop_read` proof. `skipped` means no provider execution happened and does not erase an existing class proof; `failed` replaces that class record and removes certification. Unsupported provider/class pairs return `unsupported_workflow_class=true` and do not overwrite previous evidence.

Use `super-browser live-test --provider fixtures` to run local browser fixtures for login, infinite scroll, draft-only forms, social feed scanning plus comment drafting without publishing, lead-generation extraction to local output without CRM/email actions, modal handling, upload selection, blocked pages, and resume recovery.

Use `workflow_class=external_write_gate` to prove a provider-locked post/comment-style task stops in `awaiting_approval` before any provider execution starts. This is a safety-gate proof; it does not approve or execute a real external write.

## Execution Reports

Normal runs execute the primary provider and then planned fallbacks until one succeeds or all stop. If an adapter raises unexpectedly, treat it as a redacted failed provider attempt with `provider-exception.json` metadata; fallback providers may still run. If the runtime execution boundary raises after a run is claimed, treat `runtime-exception.json` plus the failed `run-report.json` as execution evidence; the lease should be cleared and external-write retry approval gates still apply. Inspect `run-report.json` or `super-browser verify <run-id>` to see every provider attempt, blocked reason, selected provider, artifact manifest, timeout checks, cost estimate, `plan_sha256`, and confidence.

`super-browser verify <run-id>` actively checks artifact paths, SHA-256 hashes, the run-report plan fingerprint, provider sequence constraints, final-provider/attempt consistency, approval id/stage/fingerprint/decision integrity, and `run-report.json`, reports provider cost band and trace links, lists failures, and writes `verification-report.json` when a report directory exists. Inspect `plan_integrity` before trusting artifacts; a mismatch means the stored run plan and run report do not match. Handoff and direct resume treat `plan_integrity.status=mismatch` or `missing`, verifier failures `missing_run_report`, `missing_artifact_path`, `artifact_hash_mismatch`, `status_mismatch`, impossible final-provider/attempt evidence, provider constraint failures, and `approval_integrity.status=missing`, `mismatch`, `missing_fingerprint`, `missing_approval_id`, `missing_required_before`, `invalid_required_before`, `missing_decision_metadata`, or `unknown_status` as unsafe to resume. Inspect `approval_integrity` before resuming approved runs; a mismatch means the approved action no longer matches the current plan. Inspect `policy_guard` for target scope, approval state, safety events, blocked reasons, and duplicate-write retry state before trusting or retrying a run.

Use `super-browser handoff <run-id>` or `handoff_browser_run` when another agent needs the same verifier policy guard and approval integrity in a compact, read-only package. Treat `approval_status=missing` or `approval_integrity.status=mismatch` as a broken approval-gate record, not as permission to run.

In `write_retry_guard`, `fresh_retry_approval_required=true` means an approved external-write attempt already started and the next resume must create a `provider_retry` approval before any provider can try the write again.

Agent-facing CLI/MCP output, saved reports, raw HTTP text/JSON bodies, provider output JSON, and page text artifacts redact cookies, authorization headers, API keys, bearer tokens, token query parameters, passwords, and client secrets. Provider session IDs stay visible when they are needed for debugging. Binary raw HTTP bodies are preserved with metadata.

Do not place credentials in a URL. Super Saiyan Browser rejects starting URLs with embedded username/password credentials, and redaction strips URL userinfo if a provider returns it in logs or artifacts.

Use local `file://` URLs only with Playwright/local fixtures. Super Saiyan Browser extracts local file URLs from either the explicit URL field or the goal text, will not route them to cloud providers or raw HTTP, and runtime provider-sequence checks block stale or hand-built file-URL plans before provider dispatch. Raw HTTP supports only `http://` and `https://` and must include that endpoint during planning. `local_file` targets require approval because they can expose machine data.

Inspect `target_scope` in every plan. `loopback`, `private_network`, `link_local`, and `local_file` are not ordinary public-web targets and are routed through council mode for explicit review. `private_network`, `link_local`, and `local_file` targets require approval before execution; `loopback` stays available for local fixtures and development tests.

Raw HTTP redirects are target-scope checked before they are followed. A redirect into `loopback`, `private_network`, `link_local`, or `local_file` is blocked unless the run was originally planned for that same scope.

Playwright-backed browser adapters target-scope check browser requests before navigation can complete. Local Playwright and Steel CDP block redirects or subresources into sensitive scopes unless the run was planned for that scope. Treat `browser_request_target_scope` as a safety stop and inspect its metadata before replanning.

Raw HTTP, URL-capable remote/desktop providers, and Playwright-backed browser guards also resolve `public_web` hostnames at execution time. If DNS resolution returns loopback, private-network, or link-local addresses, or local DNS resolution fails and the target cannot be verified, stop and replan for the real target scope instead of retrying the public-web run. Treat `provider_url_resolved_target_scope` as a remote/desktop-provider safety stop before the URL was sent to the provider. Target-scope and DNS safety stops are non-resumable; create a new run or replan instead of calling resume on the blocked run.

## References

- `references/provider-matrix.md`
- `references/routing-playbook.md`
- `references/cost-model.md`
- `references/security-and-approval-policy.md`
- `references/live-test-matrix.md`
