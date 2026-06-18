# Super Saiyan Browser Routing Playbook

## Capability-first routing

Routing starts with one question: **what does the task need?** Capabilities decide which providers *can* do the job; the escalation rank only orders equally capable providers from cheapest/most deterministic to most expensive.

| What do you need? | Capability | Providers that have it |
| --- | --- | --- |
| Deterministic local browser, fixtures, `file://` | local runtime | `playwright` only |
| Search engine results (SERP) | `supports_serp` | `brightdata-serp` only |
| Structured platform records by URL | `supports_structured_extract` | `brightdata-dataset` |
| One-shot anti-bot URL unlock | `supports_unlocked_http` | `brightdata-unlocker` |
| Anti-bot hardened browsing | `supports_anti_bot` | `browser-use`, `brightdata-unlocker`, `brightdata-browser`, `brightdata-dataset`, `hyperbrowser`, `steel`, `browserbase` (docs-only) |
| CAPTCHA solving | `supports_captcha` | `browser-use`, `brightdata-unlocker`, `brightdata-browser`, `hyperbrowser`, `steel` |
| Authenticated sessions / logins | `supports_auth` | `browser-use`, `brightdata-browser`, `airtop`, `hyperbrowser`, `steel`, `orgo` |
| Persistent named profiles (reusable logged-in state) | `supports_profiles` | `browser-use`, `airtop`, `hyperbrowser`, `steel` |
| Upstream/residential proxy injection | `supports_proxy_injection` | `playwright`, `hyperbrowser`, `steel` |
| Fleet / many parallel sessions | `supports_fleet` | `hyperbrowser`, `steel` |
| Full desktop / OS apps / terminal | `supports_desktop` | `orgo` only |
| Long-running / recurring jobs | `supports_long_running` | `browser-use`, `airtop`, `hyperbrowser`, `steel`, `orgo` |
| Raw HTTP / JSON API fetch (no rendering) | `supports_raw_http` | `decodo-http` only — separate lane, never in browser fallbacks |

Hard filters run first: raw HTTP goals with a concrete `http://`/`https://` endpoint go to the `decodo-http` lane, `file://` targets go to Playwright only, desktop terms route to Orgo, and providers missing a required capability are scored out.

## Escalation rank (the cost tie-breaker)

When several providers satisfy the capabilities, `rank_providers()` breaks the tie with an **escalation rank** — a cost/determinism preference, not the routing model:

| Rank | Providers | Why this order |
| --- | --- | --- |
| -2 | `brightdata-serp` | Dedicated SERP lane |
| -1 | `decodo-http` | Raw HTTP only |
| 1 | `playwright`, `browser-use`, `brightdata-unlocker`, `brightdata-dataset` | Local / default cloud / cheap unlock / structured extractors |
| 2 | `brightdata-browser`, `hyperbrowser`, `airtop` | Cloud scale and interactive browser |
| 3 | `steel` | Hosted Chromium sessions |
| 4 | `orgo` | Full desktop VMs — most expensive, most capable |

Fallbacks walk **down** the ladder (1 → 2 → 3 → 4) among capable providers. `rank_providers()` applies rank bonuses, prefers Playwright at rank 1 only when the task is not anti-bot and not auth-required, and excludes Decodo from browser candidate sets unless the task is raw HTTP.

## Default Flow

1. Orchestrator receives the user request.
2. CLI/MCP infers task flags: auth, anti-bot, desktop, raw HTTP, long-running, external write, target scope, and optional provider timeout.
3. Planner ranks providers and identifies missing env vars.
4. Planner emits `council_report` with specialist recommendations and review loops.
5. Orchestrator presents the plan and approval gates.
6. Execution creates a durable run record.
7. Execution tries the primary provider and then planned fallbacks until one succeeds or all stop.
8. Verifier checks artifacts, `run-report.json`, writes `verification-report.json`, and returns confidence.

External-write detection includes posts/comments/replies/responses, email sends, messages/DMs, non-search/state-changing form submissions, uploads, social reactions, upvotes/downvotes, quote/repost/share-to-story actions, repo stars/watches/forks, bookmarks/saves/pins/favorites, follows/connections, group joins/creation, event/page creation, request/invite/connection accepts/declines/removals/cancellations/confirmations, follower/friend/member removals, RSVPs, event attendance/check-ins/interested/going marks, reports/blocks/mutes, notification toggles, message/email archive/read-state changes, tags/mentions, booking/scheduling, applications, subscriptions, reviews, poll votes, CRM lead/contact/customer creation, assignment, enrollment, stage, status, list, campaign, or sequence changes, project/repository issue, ticket, task, card, pull-request, and repo changes, cloud file/folder/document creation, renames, moves, copies, sharing/access/permission/public-visibility changes, app/integration install/authorize/connect changes, settings/preference saves, API-key/token creation, rotation, or revocation, secret reveal/copy requests, webhook creation or updates, deployment creation/promotion/rollback/redeploys, DNS record/nameserver changes, environment-variable changes, billing trial/plan/payment-method changes, trading orders, asset sales, swaps, staking, unstaking, position opens/closes/liquidations, withdrawals, deposits, fund transfers, ACH/wire/bank transfers, bank/wallet/brokerage/payout account changes, legal signatures/certifications/attestations, tax and court filings, insurance claim/policy changes, benefits or health-plan enrollment changes, prescription refills, medical form/record delivery, passport/visa/government-ID actions, voter registration, regulated address changes, emergency contact changes, workspace/channel/server/community/page creation, rename, archive, or unarchive changes, member additions, kicks, bans, unbans, role changes, thread/comment locks, cart/basket/bag/wishlist/waitlist additions/removals/quantity changes, checkout address changes, promo/coupon/offer actions, order placement/cancellation/returns/refunds/payments, ad creation/boosting/promotion, purchases/bids/donations/checkouts, profile/account changes, and destructive account actions. Hyphenated content terms such as "follow-up" do not count as the platform action "follow" unless the request actually asks for a follow/following action. Business/content phrases such as "lead magnet," "invite template," "posting schedule," "apply a filter," "book notes," or "review summary" stay non-external unless the request also asks for a real site/account state change. Public documentation, help articles, guides, policy pages, best-practice pages, examples, and local notes about sharing, OAuth, tokens, auth, integrations, API keys, webhooks, DNS records, environment variables, billing, trading, banking, ACH/wire transfers, payouts, legal forms, tax filing, insurance claims, prescriptions, medical records, passports, visas, government IDs, channels, workspaces, roles, or moderation stay read-only when the full request stays reference-only. Creating local lead/contact/prospect/customer lists, CSVs, JSON files, or run artifacts from extracted data is local output; writing or syncing those records into CRM, Salesforce, HubSpot, Pipedrive, Zoho, Apollo, campaigns, sequences, or pipelines is an external write.

Undo and removal variants are also external writes: unlike/unreact, unbookmark/unsave/unfavorite, unstar/stop watching, trash/restore cloud files, cancel/reschedule calendar events, cancel scheduled posts/messages/emails, remove CRM records from campaigns or sequences, and unenroll contacts.

Read-only scanning of visible public posts, comments, forum messages, and group content remains a read task only when the full request stays read-only. Reading personal inboxes, DMs, private messages, or private/member-only content is credential-bearing. A browse/read/search/list prefix does not neutralize a later write: scanning plus posting, commenting, replying, responding, sending, liking, following, connecting, submitting, CRM updates, cart/order/payment/trading/banking/payout changes, legal/government/health/insurance/identity changes, project/repository updates, cloud-file/sharing/integration/settings changes, secret/API-key changes, webhook/deployment/DNS/environment-variable changes, billing/payment-method changes, workspace/channel/role/moderation changes, thread locks, notification toggles, archive/read-state changes, member removals, or clicking/tapping/pressing/selecting/activating final write controls is an external write.

Submitting public search, filter, or sort forms only to fetch visible public results is read-only when the query does not include credentials, private/personal data, or another external action. Public documentation, help articles, guides, policy pages, best-practice pages, examples, and local notes about sharing, OAuth, tokens, auth, integrations, API keys, webhooks, DNS records, environment variables, billing, trading, banking, ACH/wire transfers, payouts, legal forms, tax filing, insurance claims, prescriptions, medical records, passports, visas, government IDs, channels, workspaces, roles, or moderation are also read-only when the full request stays reference-only. These exceptions do not cover a later like, save, bookmark, share, follow, connect, CRM update, cart/order/payment/trading/banking/payout change, legal/government/health/insurance/identity change, project/repository update, cloud-file/sharing/integration/settings change, secret/API-key change, webhook/deployment/DNS/environment-variable change, billing/payment-method change, workspace/channel/role/moderation change, notification toggle, message/email state change, or other external write in the same request. Lead, contact, application, checkout, signup, comment, message, quote, demo, pricing, upload, payment, registration, review, poll, booking, appointment, reservation, subscribe, and unsubscribe forms remain external writes.

Local delivery wording such as "send me a summary" or "send us the report" remains read-only only when it is not bundled with an external action. If the same request later asks to post, email, DM, submit, like, follow, or press a final write control, classify the whole task as an external write.

## Direct Mode

Use direct mode for obvious, low-risk jobs:

- Read-only extraction from ordinary pages.
- Local test automation.
- Raw HTTP against a concrete `http://` or `https://` JSON/API endpoint.
- Screenshot or DOM inspection.

Direct mode still creates a plan and run record.

Direct plans still include `council_report` with one classification loop. Council plans include three loops: classification, provider sequence, and safety/verification.

## Council Report

Every plan includes:

- `selected_sequence`: primary provider plus ordered fallbacks.
- `specialists_consulted`: every provider specialist with recommendation, confidence, cost band, stability, required env vars, missing env vars, docs URL, and do-not-use notes.
- `review_loops`: one loop for direct plans and three loops for council plans.
- `planner_decision`: final primary/fallback sequence, missing env vars, allowlist, max cost, timeout, estimated cost floor, budget status, and approval status.
- `target_scope`: `public_web`, `loopback`, `private_network`, `link_local`, `local_file`, or `none`.
- `cost_estimate`: selected-provider floor, fallback floor, worst-case floor, budget status, confidence, and routing notes.
- `approval_gate`: whether Publishing Safety must gate the run.

## Provider Constraints

Use constraints when a job has hard routing or cost limits:

```bash
super-browser plan --goal "Extract public data" --allow-provider playwright --max-cost-usd 0
super-browser run --goal "Fetch a slow endpoint" --url "https://example.com/data.json" --timeout-seconds 60
```

- `--allow-provider` is strict and can be repeated.
- `--max-cost-usd` filters by Super Saiyan Browser cost floors.
- `--timeout-seconds` sets an integer provider execution ceiling and is included in the task, planner decision, handoff output, provider metadata, and verification checks.
- If no provider satisfies the constraints, planning returns an error rather than selecting a disallowed provider.
- The planner avoids URL-required providers when no starting URL is available. Raw HTTP/API tasks require a concrete `http://` or `https://` starting URL; if the endpoint is missing, planning fails instead of silently switching to a browser provider. The stored task payload and provider sequence are re-checked by verifier, handoff, direct `resume`, and low-level `execute_plan()`. Invalid task constraints, embedded URL credentials, URL-derived target-scope mismatches, unknown providers, providers outside `providers_allowed`, non-Playwright providers for `file://` URLs, URL-required primary providers without a starting URL, raw HTTP without an HTTP endpoint, and providers over `max_cost_usd` are blocked before adapter dispatch.
- URL extraction supports `http://`, `https://`, and local `file://` targets in the goal text. URLs embedded in prose or Markdown goals have common trailing delimiters stripped, including `>`, `]`, quotes, and sentence punctuation. Explicit URL fields must not contain raw whitespace; percent-encode spaces as `%20`. A `file://` target embedded in natural language is classified as `local_file`, routed only to Playwright, and approval-gated the same as an explicit URL argument.

Before executing paid or live providers, run `super-browser env-checklist` or MCP `env_checklist` to see required and optional setup without exposing values. Then run `super-browser doctor` or MCP `browser_doctor`. Route from `readiness_status`, not just env presence:

- `usable_now=true` means the adapter can be attempted.
- `production_ready=true` means the provider is proven only for `production_ready_scope`; inspect `certified_workflow_classes` before relying on it.
- `supported_live_workflow_classes` lists what the built-in provider tests can prove; `uncertified_workflow_classes` lists supported classes without fresh evidence.
- `ignored_unsupported_evidence_workflow_classes` lists persisted evidence classes that were ignored because the provider cannot certify those workflow classes.
- `ignored_provider_mismatch_evidence_workflow_classes` lists persisted evidence classes that were ignored because the embedded evidence provider does not match the provider being certified.
- `requires_live_test_before_production=true` means setup is present but no production proof exists yet.
- `requires_live_test_before_broader_production=true` means at least one class is certified but another supported class still needs a live test.
- `production_blockers` lists the exact missing setup or workflow-class evidence.

Before claiming a deployment is production-ready, run `super-browser production-readiness` or MCP `production_readiness`. A blocked report is authoritative: do not override missing env vars, stale evidence, ignored evidence, or uncertified workflow classes with planner confidence or vendor claims.

Before handing Super Saiyan Browser to another agent or cutting a release, run `super-browser bundle-manifest` or MCP `bundle_manifest`. Treat the manifest as the bundle inventory: it records required path status, executable entrypoints, provider names, skill names, MCP tools/resources, and SHA-256 hashes for included files while excluding local secrets, state, caches, logs, sqlite files, symlinks, dependency folders, and build output.

- `live_test_passed` means fresh persisted live-test evidence exists in `latest_live_test` for the listed workflow class.
- `live_test_stale` means the provider needs a new live test before production use.
- `configured_live_test_required` and `configured_live_test_recommended` mean the next action is the provider live test.
- `usable_direct_http_no_proxy` means `decodo-http` can fetch direct raw HTTP for a supplied HTTP endpoint, but residential proxy routing still needs `DECODO_PROXY`.

Workflow-class evidence matters. A provider with `certified_workflow_classes=["general_read"]` is not proven for social posting, anti-bot, authenticated, or desktop work. Doctor filters certification to the provider's supported workflow classes and embedded evidence provider identity, so stale, copied, or hand-built evidence cannot make `decodo-http` look certified for `general_read`, make a browser-only provider look certified for desktop work, or certify one provider with another provider's evidence. Run the matching provider live test class before production use.

## MCP Contract

Use `super-browser init-mcp` to print an MCP config for the current checkout, `super-browser init-mcp --path <config.json> --merge` to add Super Saiyan Browser without removing existing servers, or `super-browser init-mcp --path <config.json> --force` to replace the file. MCP-only agents can use `init_super_browser_mcp` for the same config generation and write/merge behavior, and `install_super_browser_skill` to install the self-contained bundle. Source and installed-bundle configs include absolute `cwd`, server wrapper path, and `SUPER_BROWSER_REPO_ROOT`. Normal Python package configs launch `python -m super_browser.mcp_server` and point `SUPER_BROWSER_REPO_ROOT` at the packaged `share/super-browser` asset tree when it is present.

Every MCP tool advertises an `inputSchema` in `tools/list`. The server validates:

- required fields such as `goal` and `run_id`
- provider enum values for `providers_allowed` and live tests
- `optimize` enum values
- numeric `max_cost_usd`
- integer `timeout_seconds`
- boolean execution flags
- unsupported arguments

Invalid MCP calls fail before a runtime action is dispatched. Malformed `tools/call` params, missing tool names, blank tool names, non-object tool arguments, and unexpected exceptions from known tools return structured `isError` tool results with redacted `error` and `error_type` so agents can self-correct without treating the server as crashed. Malformed `resources/read` params, missing resource URIs, and blank resource URIs return clear protocol errors. Well-formed JSON-RPC notifications without an `id`, including `notifications/initialized`, are consumed without a response.

## Fallback Execution

Plans are executable provider sequences, not static recommendations. Normal `run` execution tries:

1. `primary_provider`
2. each unique `fallback_provider` in order

Every attempt records:

- provider
- status: `complete`, `blocked`, or `failed`
- error, if any
- artifact count
- verification checks
- timeout checks, when a task sets `timeout_seconds`

Unexpected provider adapter exceptions must be captured as `failed` attempts with redacted error text and `provider-exception.json` metadata. They are provider attempt failures, not orchestration crashes, so fallback execution continues when another planned provider remains. If all providers raise, the final result is a structured failed run report with every exception represented as an attempt.

Unexpected runtime execution exceptions after a run is claimed must also become durable evidence. Record `runtime-exception.json`, write a failed `run-report.json` with the selected provider and plan fingerprint, clear the execution lease through the normal terminal-result path, and keep any external-write retry gate active.

The final artifact list includes `run-report.json`. Provider-specific live tests intentionally disable fallback execution so they prove the named provider. Provider adapters enforce `timeout_seconds` through native browser, HTTP, SDK, or CLI timeouts; do not implement timeout by launching an unkillable background worker.

When a failed or blocked run is safely resumed into another provider execution, the run store keeps the durable plan artifact and replaces the previous execution artifact manifest with the fresh provider result. Events keep the retry history. Verifier reads the newest `run-report.json` artifact, not the first one, so stale failed evidence cannot poison a successful retry and overwritten artifact paths cannot keep stale hashes in handoff.

Provider sequence constraints are execution policy, not just planning hints. Before any adapter is called, `execute_plan()` validates the task payload and verifies that the stored target scope still matches the URL-derived target scope, URL-required primary providers have a starting URL, and that the primary and fallback providers still satisfy the task's allowlist, local file routing, known-provider set, and cost ceiling. A violation returns a blocked run report with `reason=provider_constraints` and zero provider attempts.

Verifier run ids, report ownership, and artifact paths are also part of execution evidence. `verify`, `handoff`, and direct resume require safe generated `run_*` ids, require ordinary terminal failed/blocked/complete runs to have a readable `run-report.json`, require `run-report.json` to carry the same `run_id` as the saved run record, and trust artifacts only when they resolve inside `.super-browser/artifacts/<run-id>/` or the configured `SUPER_BROWSER_STATE_DIR` equivalent. A dot-segment or otherwise invalid run id is reported as `invalid_run_id`; a terminal run without a readable report is reported as `missing_run_report`; a copied or stale run report from a different run is reported as `run_report_run_id_mismatch`; a missing artifact path is reported as `missing_artifact_path`; a changed artifact hash is reported as `artifact_hash_mismatch`; a forged or stale artifact path outside that directory is reported as `untrusted_artifact_path`, is not read or hashed, and makes handoff/resume unsafe before provider retry. `missing_run_report` is allowed only for stale execution recovery before the recovered attempt runs, or for the safe state transition that creates a fresh external-write retry approval without executing the provider.

Raw HTTP redirects are part of provider execution policy. A redirect into `loopback`, `private_network`, `link_local`, or `local_file` is blocked unless the run was originally planned for that same target scope. Treat a `raw_http_redirect_target_scope` event as a safety stop, not as an ordinary network failure.

Playwright-backed browser requests are also part of provider execution policy. Local Playwright and Steel CDP install a request guard before navigation; if a browser redirect or subresource request targets `loopback`, `private_network`, `link_local`, or `local_file` from a different planned scope, execution returns `blocked` with `browser_request_target_scope` metadata instead of page artifacts. If browser close/disconnect fails after a successful capture, keep the captured artifacts and treat `browser_close_failed` as a warning, not as proof the automation failed.

Raw HTTP, URL-capable remote/desktop providers, and Playwright-backed browser guards resolve `public_web` hostnames before continuing. If DNS resolution returns a loopback, private-network, or link-local address, or local DNS resolution fails and the target cannot be verified, the run is blocked with target-scope evidence. Treat this as DNS-rebinding and split-horizon protection and replan for the real target scope instead of retrying. For remote/desktop providers, `provider_url_resolved_target_scope` means the target URL was blocked before it was sent to Browser Use, Orgo, Airtop, Hyperbrowser, or Steel. Target-scope and DNS safety stops are non-resumable; handoff returns `resume.safe_to_resume=false`, and direct resume records `resume_blocked` until a fresh run or replan exists.

Provider transport override env vars are preflighted separately from target URLs. `ORGO_API_BASE`, `AIRTOP_API_BASE`, `HYPERBROWSER_API_BASE`, and `STEEL_CDP_URL` may use loopback HTTP/WS for self-hosted local providers, but private-network/link-local provider endpoints or insecure remote HTTP/WS require explicit override env vars before credentials are sent.

Approval-gated plans must use the durable run lifecycle. The low-level `execute_plan()` adapter API re-checks task policy, blocks approval-required plans by default, and requires structured `approval_context` from runtime code after approval has been recorded. A bare approval boolean must not be treated as sufficient proof.

## Verification Report

`super-browser verify <run-id>` and `verify_browser_run` actively audit:

- run record status
- selected provider and provider cost band
- cost estimate and budget status
- `run-report.json` parseability
- newest `run-report.json` selection when multiple report artifacts exist
- `run-report.json` ownership: report `run_id` matches the saved run id
- `plan_integrity`: stored run plan fingerprint matches `run-report.json` `plan_sha256`
- provider attempts and fallback selection
- artifact path existence
- trace/live/recording URLs
- approval state
- `policy_guard`: target scope, approval state, external-write/auth/draft/long-running flags, safety events, blocked reasons, and duplicate-write retry state
- `approval_integrity`: whether the latest pending or approved request still matches the current plan fingerprints
- provider sequence constraints: target-scope mismatch, allowlist, known provider, file URL, and max-cost violations
- corrupt stored-run payloads surfaced as `store_payload_corrupt`
- failures and confidence

If `get`, `runs`, `get_browser_run`, or `list_browser_runs` returns `store_payload_corrupt`, treat the row as failed evidence, not a provider failure. Direct `resume` must record `resume_blocked` before provider dispatch because the stored plan cannot be trusted; create a new run or inspect the durable state store instead.

`approval_status=missing` means the plan required approval but no approval request record exists. `approval_integrity.status=mismatch` means the current plan no longer matches the pending or approved approval fingerprint. Treat any broken approval evidence as a policy gate failure and fix the run record or create a new run; do not execute or resume it as approved. Direct `resume` must record `resume_blocked` before any execution claim when `approval_integrity.status` is `missing`, `mismatch`, `missing_fingerprint`, `missing_approval_id`, `missing_required_before`, `invalid_required_before`, `missing_decision_metadata`, or `unknown_status`.

Handoff compact safety fields must be derived from verifier `policy_guard`. If the saved plan says `task.external_write=false`, `task.requires_auth=false`, `task.draft_only=true`, or `approval_required=false` but policy classification says the goal is still an external write, credential-bearing workflow, or otherwise approval-gated, handoff must report the policy-derived risk and mark resume unsafe until approval evidence is repaired or a new run is created. If the saved plan says `task.draft_only=false` but policy classification says the goal is explicitly draft-only, handoff should still report the policy-derived draft-only state.

Verifier and handoff must also surface approval freshness. If `approval_expiry.status=expired`, handoff should report `resume.safe_to_resume=true` and `resume.will_execute_provider=false`: resume is allowed only to create a fresh approval request before provider execution.

Provider prompts must follow the same policy-derived rule. A browser-provider prompt for an explicit draft-only goal must include the stop-before-publish/send/submit instruction and the broader stop-before-external-state-change instruction for follows, connections, reactions, shares, CRM/cart/order/payment/project/repository/cloud-file/sharing/integration/settings/notification/message-state/member/account changes, and final write controls, even if a stale stored run has `task.draft_only=false`; a real external-write goal must not inherit draft-only prompt text merely because a stale stored run has `task.draft_only=true`. Read-only prompts must say to navigate/search/scroll/inspect/extract only. Authenticated read/navigation prompts must say the session or credentials may be used only for the requested read, navigation, extraction, or inspection. External-write prompts must say provider execution is allowed only after durable runtime approval has been verified and must perform only the exact requested action. These prompts are a provider-control layer, not approval proof; the durable run lifecycle and verifier checks remain authoritative.

`run_id_integrity.status=invalid` means the saved payload has a run id that cannot safely define a run artifact directory. `plan_integrity.status=mismatch` means the stored run plan and `run-report.json` no longer match. Verifier failure `status_mismatch` means `run-report.json` `final_status` no longer matches the saved run status, except during an approved external-write retry transition where the old failed report will be replaced by the new provider attempt. Verifier failures `invalid_run_id`, `missing_run_report`, `missing_artifact_path`, `artifact_hash_mismatch`, `untrusted_artifact_path`, `run_report_run_id_mismatch`, `run_report_final_provider_not_planned`, `run_report_complete_without_complete_attempt`, `run_report_final_provider_attempt_mismatch`, `run_report_final_provider_attempt_missing`, and `run_report_final_status_attempt_mismatch` mean the report's run owner, final provider, artifact paths, hashes, or attempt history cannot be trusted. Treat these as evidence corruption or stale handoff state, not as provider failures to retry. Handoff must mark `resume.safe_to_resume=false` when `run_id_integrity.status` is `invalid`, when `plan_integrity.status` is `mismatch` or `missing`, or when verifier failures include any of those run-report/artifact integrity failures outside the explicit stale-recovery and retry-approval transitions. Direct `resume` must record `resume_blocked` before any provider retry.

Provider constraint failures mean the current stored task payload or provider sequence no longer satisfies the router contract. Treat `provider_target_scope_mismatch`, `provider_allowlist_violation`, `provider_file_url_constraint_violation`, `provider_missing_url_constraint_violation`, `provider_cost_constraint_violation`, `provider_constraint_unknown_provider`, and `provider_constraint_invalid_task` as unsafe to resume. Create a new run or fix the plan through normal planning instead of retrying the provider.

When a run has a report directory, verification writes `verification-report.json` next to `run-report.json`.

## Resume Semantics

`resume` is an execution command, not just a lookup:

- `planned`, `approved`, `blocked`, `failed`, and stale `executing` runs execute again through the provider sequence only when approval, write-retry, lease, run-report integrity, and provider sequence gates allow it.
- `awaiting_approval` runs remain stopped and record `resume_blocked`.
- `denied`, `complete`, and active `executing` runs are returned as no-ops.
- Execution is atomically claimed in the SQLite run store before provider calls begin; stale concurrent workers must return the current run instead of starting another provider call.
- Executing runs carry a lease. Active leases are not resumed; the no-op observation is saved without clearing the lease so another agent can see that resume was attempted. Expired leases record `stale_execution_recovered`, move to a resumable failed state, and then resume. Terminal provider results and captured runtime execution exceptions clear the lease. The default lease is 4 hours, or 24 hours for tasks classified as long-running from current policy classification as well as the stored flag, and can be overridden with `SUPER_BROWSER_EXECUTION_LEASE_SECONDS`.
- Execution leases are duplicate-worker guards, not provider operation timeouts. Use `timeout_seconds` when the task needs a hard per-provider runtime ceiling.
- Approved external-write runs that already recorded `external_write_attempt_started` do not retry automatically; resume creates a fresh `provider_retry` approval and records `external_write_retry_blocked`.
- Runs with `plan_integrity.status=mismatch` or `missing` do not retry automatically; resume records `resume_blocked` with `reason=run_report_plan_integrity`.
- Runs with verifier failure `status_mismatch` do not retry automatically; resume records `resume_blocked` with `reason=run_report_status_integrity`.
- Runs with verifier failures for invalid run ids, missing run reports, missing artifact paths, artifact hash mismatches, mismatched run-report run ids, outside artifact paths, impossible final-provider evidence, or impossible attempt evidence do not retry automatically; resume records `resume_blocked` with `reason=run_report_evidence_integrity`.
- Runs with provider sequence constraint failures do not retry automatically; resume records `resume_blocked` with `reason=provider_constraints`.
- Runs with `approval_integrity.status=missing`, `mismatch`, `missing_fingerprint`, `missing_approval_id`, `missing_required_before`, `invalid_required_before`, `missing_decision_metadata`, or `unknown_status` do not execute; resume records `resume_blocked` with `reason=approval_integrity`.
- Handoff `resume.safe_to_resume=true` can still have `resume.will_execute_provider=false`. That means the resume call is safe as a state transition, but it will create an approval gate rather than start provider execution.
- Use `super-browser get <run-id>` or `get_browser_run` for read-only lookup.
- Use `super-browser handoff <run-id>` or `handoff_browser_run` for a compact read-only package with run summary, provider readiness, approval state, verifier summary including `policy_guard` and `approval_integrity`, commands, docs, and next steps.
- Use `super-browser runs --status <status> --limit 20` or `list_browser_runs` for read-only run discovery after handoff, compaction, or a lost run id.
- Run lists are compact by default. Use CLI `--details` or MCP `include_details=true` only when full payload lists are needed.
- Empty lookup/list calls do not create `.super-browser` state.
- MCP tool calls return `structuredContent` as the machine-readable result. Prefer that over parsing the text content.
- Recoverable MCP tool failures and unexpected exceptions from known tools return `isError: true` with redacted structured error details and `error_type` so agents can self-correct. This includes missing runs, rejected arguments, malformed `tools/call` params, missing or blank tool names, non-object tool arguments, and runtime failures inside known tools. Unknown tools, unsupported protocol methods, malformed JSON, and non-object JSON-RPC requests remain protocol errors. Malformed JSON or non-object requests return a `null` id and must not inherit an earlier request id.
- MCP `resources/list` and `resources/read` expose read-only provider docs, role skills, and routing playbooks for agents that only have MCP access.

## Council Mode

Use council mode when any of these are true:

- Authenticated session is needed.
- Anti-bot risk is present.
- Full desktop/computer use is needed.
- The job is long-running or recurring.
- External write or credential use is possible.
- The target is loopback, private-network, link-local, or local-file instead of ordinary public web. Private-network, link-local, and local-file targets also require approval before execution; loopback stays available for local fixture and development workflows.
- The expected provider cost is material.
- The route is unclear or provider reliability is unproven.

## Routing Recipes

### Simple extraction

Route: Playwright -> Hyperbrowser -> Browser Use.

### Authenticated browsing

Route: Browser Use profiles -> Airtop sessions -> Steel sessions.

### Anti-bot workflow

Route: Browser Use -> Hyperbrowser -> Steel -> Orgo only if desktop fallback is justified.

### Raw data/API endpoint

Route: Decodo HTTP for supplied HTTP endpoints -> direct requests without proxy -> browser only if rendering is necessary.

### Full desktop

Route: Orgo -> local computer/container backend if available -> manual escalation.

### Publishing/commenting/messaging

Route: Planner chooses browser provider, Publishing Safety gates the external write, Verifier confirms no write happened before approval.
