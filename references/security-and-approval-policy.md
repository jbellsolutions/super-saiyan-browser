# Security And Approval Policy

Super Saiyan Browser can interact with real websites and computers. Policy must be enforced in code and in agent instructions.

## Risk Classes

| Risk | Examples | Default |
| --- | --- | --- |
| Read-only | Open page, screenshot, extract text, inspect DOM | Allow |
| Mutating local/browser | Type draft, click navigation, fill fields without submit | Allow if scoped |
| External write | Post, comment, reply/respond, DM, email, submit non-search/state-changing form, upload file, like/react/upvote/downvote/follow/connect/share/quote/repost/story, star/watch/fork repos, bookmark/save/pin/favorite content, join/create group, create event/page, accept/decline/remove/cancel/confirm requests/invites/connections, remove followers/friends/members, RSVP/check in/interested/going, report/block/mute, notification toggles, message/email archive/read-state changes, tag/mention, book/schedule, request info/demo/quotes/pricing, apply, subscribe, review, poll vote, CRM lead/contact/customer create/assign/enroll/stage/list updates, project/repository issue, ticket, task, card, pull-request, and repo changes, cloud file/folder/document creation, renames, moves, copies, sharing/access/permission/public-visibility changes, app/integration install/authorize/connect changes, settings/preference saves, API-key/token creation, rotation, or revocation, secret reveal/copy requests, webhook creation or updates, deployment creation/promotion/rollback/redeploys, DNS record/nameserver changes, environment-variable changes, billing trial/plan/payment-method changes, trading orders, asset sales, swaps, staking, unstaking, position opens/closes/liquidations, withdrawals, deposits, fund transfers, ACH/wire/bank transfers, bank/wallet/brokerage/payout account changes, legal signatures/certifications/attestations, tax and court filings, insurance claim/policy changes, benefits or health-plan enrollment changes, prescription refills, medical form/record delivery, passport/visa/government-ID actions, voter registration, regulated address changes, emergency contact changes, workspace/channel/server/community/page creation, rename, archive, or unarchive changes, member additions, kicks, bans, unbans, role changes, thread/comment locks, create/boost/promote ads, add/remove/change cart/basket/bag/wishlist/waitlist items, change checkout addresses, apply promo/coupon/offer actions, place/cancel/return/refund/pay orders, purchase/bid/donate/checkout, click/tap/press/select/activate final write controls | Require approval |
| Credential-bearing | Login, 2FA, OAuth, cookies, profiles, tokens, API keys, client secrets, private keys | Require approval and audit |
| Destructive | Delete, reset, purchase, cancel, account settings | Require explicit approval |

Undo and removal actions are still external writes. Unlike/unreact, unbookmark/unsave/unfavorite, unstar/stop watching, trash/restore cloud files, cancel/reschedule calendar events, cancel scheduled posts/messages/emails, remove CRM records from campaigns or sequences, and unenroll contacts all require approval.

## Approval Payload

Before an external write, show:

- Target site and URL.
- Account/profile identity if known.
- Exact content or action.
- Audience or recipient.
- Irreversible consequences.
- Provider and trace/artifact.
- Fallback if denied.

## Draft-Only Workflows

Draft-only text preparation is allowed without approval when the request explicitly says not to publish, post, comment, reply, respond, message/DM, send, or submit. Provider prompts must preserve that boundary from current policy classification, not from mutable stored `task.draft_only` alone, and tell browser agents to stop before any final publish, post, comment, reply, respond, message/DM, send, submit, upload, follow, connect, react, share, CRM/cart/order/payment/trading/banking/payout/legal/government/health/insurance/identity/project/repository/cloud-file/sharing/integration/settings/secrets/infrastructure/billing/workspace/channel/role/moderation/notification/message-state/member/account change, click, tap, press, select, or activate control. Hyphenated content terms such as "follow-up" do not count as the platform action "follow" unless the request actually asks for a follow/following action. Business/content phrases such as "lead magnet," "invite template," "posting schedule," "apply a filter," "book notes," or "review summary" stay non-external unless the request also asks for a real site/account state change. Public documentation, help articles, guides, policy pages, best-practice pages, examples, and local notes about sharing, OAuth, tokens, auth, integrations, API keys, webhooks, DNS records, environment variables, billing, trading, banking, ACH/wire transfers, payouts, legal forms, tax filing, insurance claims, prescriptions, medical records, passports, visas, government IDs, channels, workspaces, roles, or moderation stay read-only when the full request stays reference-only. Creating local lead/contact/prospect/customer lists, CSVs, JSON files, or run artifacts from extracted data is local output, not an external write; writing or syncing those records into CRM, Salesforce, HubSpot, Pipedrive, Zoho, Apollo, campaigns, sequences, or pipelines remains approval-gated. Examples:

- Draft a comment but do not publish.
- Draft a comment but do not comment.
- Write a reply in the box but do not reply.
- Type a reply into a box but do not send.
- Fill a text form draft without submitting.

Read-only scanning of visible public posts, comments, forum messages, and group content is allowed as a read task only when the full request stays read-only. Reading personal inboxes, DMs, private messages, or private/member-only content is credential-bearing and requires approval. A browse/read/search/list prefix does not neutralize a later write. If a scanning task also asks the agent to post, comment, reply, send, follow, connect, submit, update CRM state, change cart/order/payment/trading/banking/payout state, change legal/government/health/insurance/identity state, update project/repository state, change cloud-file/sharing/integration/settings state, change secrets/API keys, change webhooks/deployments/DNS/environment variables, change billing/payment methods, change workspace/channel/role/moderation state, lock a thread, toggle notifications, archive or mark messages/email, remove a member, or click, tap, press, select, or activate a like/follow/send/submit/publish-style control, treat it as an external write.

Submitting public search, filter, or sort forms only to fetch visible public results is read-only when the query does not include credentials, private/personal data, or another external action. Public documentation, help articles, guides, policy pages, best-practice pages, examples, and local notes about sharing, OAuth, tokens, auth, integrations, API keys, webhooks, DNS records, environment variables, billing, trading, banking, ACH/wire transfers, payouts, legal forms, tax filing, insurance claims, prescriptions, medical records, passports, visas, government IDs, channels, workspaces, roles, or moderation are also read-only when the full request stays reference-only. These exceptions do not cover a later like, save, bookmark, share, follow, connect, CRM update, cart/order/payment/trading/banking/payout change, legal/government/health/insurance/identity change, project/repository update, cloud-file/sharing/integration/settings change, secret/API-key change, webhook/deployment/DNS/environment-variable change, billing/payment-method change, workspace/channel/role/moderation change, notification toggle, message/email state change, or other external write in the same request. Lead, contact, application, checkout, signup, comment, message, quote, demo, pricing, upload, payment, registration, review, poll, booking, appointment, reservation, subscribe, and unsubscribe forms remain approval-gated.

Local delivery wording such as "send me a summary" or "send us the report" is read-only only when it is not bundled with an external action. A mixed request that asks for local delivery and also asks the agent to post, email, DM, submit, like, follow, or press a final write control must stop for approval.

The system must still stop before any publish, post, comment, reply/respond, send, DM, non-search/state-changing submit, upload, like, react, upvote/downvote, star/watch/fork repo, bookmark/save/pin platform content, follow/connect, share, join/leave group, accept/decline/remove request/invite/connection, remove a member, RSVP/check in/mark interested/mark going, report/block/mute, toggle notifications, archive or mark email/messages, tag/mention, book/schedule/reserve, request info/demo/quote/pricing, apply, subscribe, CRM lead/contact/customer creation, assignment, enrollment, stage, status, list, campaign, or sequence change, project/repository issue, ticket, task, card, pull-request, or repo changes, cloud file/folder/document creation, rename, move, copy, sharing/access/permission/public-visibility changes, app/integration install/authorize/connect changes, settings/preference saves, API-key/token creation/rotation/revocation, secret reveal/copy requests, webhook creation/updates, deployment creation/promotion/rollback/redeploys, DNS record/nameserver changes, environment-variable changes, billing trial/plan/payment-method changes, trading orders, asset sales, swaps, staking, unstaking, position opens/closes/liquidations, withdrawals, deposits, fund transfers, ACH/wire/bank transfers, bank/wallet/brokerage/payout account changes, legal signatures/certifications/attestations, tax and court filings, insurance claim/policy changes, benefits or health-plan enrollment changes, prescription refills, medical form/record delivery, passport/visa/government-ID actions, voter registration, regulated address changes, emergency contact changes, workspace/channel/server/community/page creation, rename, archive, or unarchive changes, member additions, kicks, bans, unbans, role changes, thread/comment locks, cart/basket/bag/wishlist/waitlist additions/removals/quantity changes, checkout address changes, promo/coupon/offer actions, order placement/cancellation/returns/refunds/payments, purchase, account change, credential use, destructive action, or click/tap/press/select/activate action on a final write button, icon, link, or control. Uploads are treated as external writes even when later form submission is not requested, because selecting a file can expose local data to a website.

Provider prompts must carry the current policy boundary in addition to the runtime gate. Read-only prompts must restrict the provider to navigation, search, scrolling, inspection, and extraction. Authenticated read/navigation prompts must restrict sessions, cookies, profiles, and credentials to the requested read or inspection. External-write prompts must state that durable runtime approval has already been verified and that only the exact requested external action may be performed. Prompt instructions help contain provider drift, but runtime approval records, adapter guards, target-scope/DNS checks, duplicate-write retry protection, verifier policy guards, and handoff approval-integrity checks are the enforcement controls.

## Logging

Every run records:

- Provider choice and fallback providers.
- Missing env vars.
- Risk class.
- Target scope: public web, loopback, private network, link-local, local file, or none.
- Approval-required flag.
- Artifact list.
- Verification confidence.
- Pending, approved, or denied approval requests.
- External-write attempt fingerprints and retry blocks.

## Combined Credential And Write Risk

Treat risk as cumulative. A task such as "use my logged-in profile to post a comment" is both credential-bearing and an external write. The external-write flag must remain set so duplicate-write retry protection applies after a failed approved attempt.

Credential-bearing browser use includes authenticated sessions, cookies, tokens, passwords, passkeys, API keys, access tokens, client secrets, private keys, service account keys, and local Chrome/browser profiles. Public profile extraction is read-only unless the task also asks to use a logged-in, authenticated, local, existing, or user-owned profile/session.

## Duplicate Write Protection

Approval requests include an `approval_id`, `required_before` stage, `action_fingerprint`, and `plan_sha256` for the exact external-write action and stored run plan. Approval must validate the pending request before recording a decision. If the approval id/stage is missing, the action fingerprint no longer matches, or the plan fingerprint no longer matches the current plan, the approval is rejected and the run remains `awaiting_approval`. Execution must use the `plan_sha256` stored on the approved record, not a freshly recomputed value, so any plan mutation after approval blocks provider execution. Approved and denied records must also keep non-empty decision actor and timestamp metadata.

Approved records are not reusable forever. Before provider execution, runtime checks the latest approved record against the approval freshness window, which defaults to 30 minutes and is configurable with `SUPER_BROWSER_APPROVAL_TTL_SECONDS`. If the approval expired, runtime must stop before provider dispatch, record `approval_expired`, return the run to `awaiting_approval`, and add a fresh pending approval for the same stage. Verifier must expose `approval_expiry`, and handoff must report that resume is safe only to create the new approval gate, not to execute a provider.

When an approved external-write attempt starts, the runtime records `external_write_attempt_started`. The runtime and verifier must derive external-write status from policy classification as well as stored task flags, so a stale or hand-built run record cannot bypass retry protection by setting `task.external_write=false` while the goal still asks for a post, comment, message, submission, account change, or other external write.

If that run later resumes after a failure or crash, Super Saiyan Browser must not reuse the old approval. It must:

- Stop execution before another provider attempt.
- Return the run to `awaiting_approval`.
- Add a fresh pending approval with `required_before=provider_retry`.
- Record `external_write_retry_blocked`.
- Report the state through verifier `write_retry_guard`.

Only after that fresh retry approval is approved may one more external-write attempt start.

Verifier `write_retry_guard.fresh_retry_approval_required=true` means a failed or blocked run has already started an approved external-write attempt and has not yet created or approved a retry gate. Handoff must report `resume.will_execute_provider=false` for that state so agents know resume is only allowed to create the fresh approval request.

## Redaction

Super Saiyan Browser redacts high-risk secrets before writing provider metadata, provider output JSON, raw HTTP text/JSON body artifacts, page text artifacts, `run-report.json`, `verification-report.json`, stored run payloads, and CLI/MCP run responses. Redacted values include:

- Authorization and proxy-authorization headers.
- Cookies and set-cookie headers.
- API keys and `x-api-key` headers.
- Bearer/basic tokens, JWT-like strings, and token env assignments.
- `token`, `access_token`, `refresh_token`, `id_token`, `api_key`, `client_secret`, `password`, `secret`, and similar query parameters.
- URL username/password userinfo.

Provider session IDs and run IDs remain visible unless they are explicitly token-like, because they are needed for debugging and provider support. Binary raw HTTP response bodies are preserved as binary artifacts with metadata; text and JSON raw HTTP bodies are redacted before being written.

Starting URLs with embedded username/password credentials are rejected before state is created. Explicit URLs with raw whitespace are rejected; agents should percent-encode spaces as `%20`. URLs extracted from prose or Markdown goals strip common trailing delimiters such as `>`, `]`, quotes, and sentence punctuation before target-scope classification. Use environment variables, browser profiles, or provider-native auth instead of URL userinfo.

Local `file://` URLs are supported only for local Playwright fixtures and local browser testing. Router URL extraction detects local file URLs in goal text as well as explicit URL fields. Router constraints keep file URLs on Playwright, and the raw HTTP adapter rejects non-HTTP schemes even for hand-built plans. Raw HTTP/API planning requires a concrete `http://` or `https://` endpoint, so missing endpoints and `file://` targets do not fall through to browser providers. `local_file` targets require explicit approval because they can expose machine data.

Provider routing constraints are security controls, not hints. The planner must avoid URL-required providers when no starting URL is available and must reject raw HTTP/API work unless an HTTP endpoint is present. Verifier, handoff, direct resume, and low-level `execute_plan()` must re-check the stored task payload plus primary/fallback sequence before provider dispatch. Malformed task constraints, embedded URL credentials, stale stored target scopes that no longer match the URL-derived scope, unknown providers, providers outside `providers_allowed`, non-Playwright providers for local `file://` URLs, URL-required primary providers without a starting URL, raw HTTP without an HTTP endpoint, and providers above `max_cost_usd` must block with provider-constraint evidence instead of calling an adapter.

Live-test evidence is a trust-scoped signal, not an unrestricted credential for production use. Doctor must filter persisted evidence to the workflow classes the provider supports and to records whose embedded provider identity matches the provider being certified before setting `readiness_status`, `certified_workflow_classes`, or `production_ready_scope`. Hand-built, stale, incompatible, copied, or provider-mismatched evidence must be listed in `ignored_unsupported_evidence_workflow_classes` or `ignored_provider_mismatch_evidence_workflow_classes` and must not make the provider appear production-ready for that class.

HTTP and file targets are classified by scope. `loopback`, `private_network`, `link_local`, and `local_file` targets force council-mode visibility so agents do not confuse localhost, intranet, metadata-service, or machine-file access with ordinary public-web browsing. Raw HTTP and Playwright metadata include the same `target_scope`.

`private_network`, `link_local`, and `local_file` targets require explicit approval before execution. This covers intranet, single-label host, RFC1918, reserved, metadata-service style addresses, and local machine files, and prevents a read-only-looking task from silently querying sensitive local infrastructure. `loopback` remains executable without approval for local fixtures and development tests.

Raw HTTP redirects are checked before they are followed. Redirects into `loopback`, `private_network`, `link_local`, or `local_file` are blocked unless the run was originally planned for the same target scope. Blocked redirect metadata includes the source URL, target URL, target scope, and blocked reason, and no response body is saved.

Playwright-backed browser providers install a request target-scope guard before navigation. Local Playwright and Steel CDP block browser redirects and subresource requests into `loopback`, `private_network`, `link_local`, or `local_file` unless the run was originally planned for that same target scope. Blocked browser-request metadata includes the URL, method, resource type, target scope, and guard install state; page text and screenshot artifacts are not reported for the blocked attempt.

Execution guards resolve `public_web` hostnames before allowing raw HTTP requests, URL-capable remote/desktop providers, or Playwright-backed browser requests to continue. If any resolved address is `loopback`, `private_network`, or `link_local`, or if local DNS resolution fails and the target cannot be verified, the run is blocked and writes target-scope evidence. Treat this as DNS-rebinding and split-horizon protection; do not retry the same public-web plan until DNS is resolvable or the task is replanned for the real target scope. Remote/desktop provider preflight uses `provider_url_resolved_target_scope` and blocks before sending the target URL to Browser Use, Orgo, Airtop, Hyperbrowser, or Steel. These target-scope and DNS safety stops are non-resumable: handoff must mark `resume.safe_to_resume=false`, and direct resume must record `resume_blocked` before any execution claim.

Provider transport overrides are credential-bearing configuration. Before using `ORGO_API_BASE`, `AIRTOP_API_BASE`, `HYPERBROWSER_API_BASE`, or `STEEL_CDP_URL`, execution validates scheme, host, URL credentials, target scope, and insecure transport. Loopback HTTP/WS is allowed for local self-hosted providers. Private-network or link-local provider endpoints require `SUPER_BROWSER_ALLOW_INTERNAL_PROVIDER_BASES=1`; insecure non-loopback HTTP/WS requires `SUPER_BROWSER_ALLOW_INSECURE_PROVIDER_BASES=1`. A blocked provider transport override means credentials were not sent.

`timeout_seconds` is an execution-control field, not a safety approval. It sets a provider operation ceiling and is recorded in task plans, handoff output, provider metadata, and verification checks. Adapters must enforce it with native browser, HTTP, SDK, or CLI timeouts rather than unbounded background workers.

## CLI And MCP

```bash
super-browser get <run-id>
super-browser handoff <run-id>
super-browser runs --status awaiting_approval --limit 20
super-browser approve <run-id> --by "human" --reason "approved exact action"
super-browser deny <run-id> --by "human" --reason "not approved"
```

Approval and denial actors and reasons are required. Empty decision actors or reasons are rejected so every approval record has a human/agent identity and audit note tied to the exact action being approved or denied.

MCP tools:

- `get_browser_run`
- `handoff_browser_run`
- `list_browser_runs`
- `approve_browser_run`
- `deny_browser_run`
- `production_readiness`
- `bundle_manifest`
- `env_checklist`
- `install_super_browser_skill`
- `init_super_browser_mcp`

`get`, `handoff`, `runs`, `get_browser_run`, `handoff_browser_run`, and `list_browser_runs` are read-only. They must not create provider attempts, approvals, resume events, or empty `.super-browser` state when no run database exists. `handoff` may compute verifier state in memory, but it must not write `verification-report.json`. Run lists return compact summaries by default; callers must explicitly request details when they need full payload lists. If a stored row payload cannot be decoded, read-only lookup/listing must surface a low-confidence failed record with `store_payload_corrupt`; it must not hide the row or crash the agent.

MCP tool annotations label read-only, setup, and execution tools for client planning, but policy is enforced in the router, runtime, and adapter guard. Clients must not treat annotations as a substitute for approval checks. MCP schemas validate `timeout_seconds` as an integer of at least 1 and reject whitespace-only string arguments before execution is dispatched. `approve_browser_run` and `resume_browser_run` are marked conservative/write-capable because `approve_browser_run` with `execute=true` and `resume_browser_run` after approval can dispatch provider execution. `env_checklist` is read-only and returns required/optional env var names plus configured/missing status without values. `bundle_manifest` is read-only and returns a redacted SHA-256 inventory for handoff and release audit. Setup tools can write local files and are marked write-aware in annotations; they do not grant provider execution approval. Skill bundle installation refuses destinations inside the source repo or destinations that contain the source repo, even when force replacement is requested. Installed bundles exclude local-only secrets, state, caches, dependency folders, logs, sqlite files, symlinks, and build output, then write `super-browser-manifest.json` so another agent can verify the installed bundle inventory. MCP config generation validates that `cwd` points to a Super Saiyan Browser repository or installed bundle with an executable `mcp/super-browser-server`; invalid bundle paths must fail before writing config files.

CLI commands return JSON on success and redacted stderr JSON with `error` and `error_type` for known Super Saiyan Browser command failures. Recoverable MCP tool failures, including missing runs, rejected arguments, malformed `tools/call` params, missing or blank tool names, non-object tool arguments, and unexpected exceptions from known tools, return `isError: true` with redacted structured error details and `error_type`. Unknown tools, unsupported protocol methods, malformed `resources/read` envelopes, malformed JSON, and non-object JSON-RPC requests remain protocol errors. Well-formed JSON-RPC notifications without an `id` are consumed without a response. Malformed JSON or non-object requests return a `null` id and must not inherit an earlier request id.

MCP `resources/list` and `resources/read` expose only allowlisted read-only markdown docs from `README.md`, `SKILL.md`, `references/`, and `skills/*/SKILL.md`. Resource paths are resolved and must stay inside a verified Super Saiyan Browser repository, installed bundle root, or packaged `share/super-browser` asset tree, so symlinks, path escapes, invalid `SUPER_BROWSER_REPO_ROOT` values, package current working directories, and unrelated project files are skipped in listings and rejected on direct reads. Resource reads must not expose arbitrary filesystem paths or create local state.

Approval does not execute by default. Immediate execution requires explicit `--execute` or MCP `execute=true`.

Resume does not override this policy. `super-browser resume <run-id>` and `resume_browser_run` must stop when the run is still `awaiting_approval`; they may continue only after approval has been recorded.

Executing runs carry a durable lease. A resume call during an active lease records a no-op and preserves the lease; it must not dispatch another provider worker. Long-running lease duration must derive from current policy classification as well as stored task flags so stale run records cannot shorten monitor, overnight, recurring, or crawl runs. When provider execution reaches a terminal result, the runtime clears the lease so later handoffs do not treat the finished run as still active.

Verifier and handoff output must expose `policy_guard` with target scope, approval-required flag, approval status, external-write/auth/draft/long-running flags, safety events, blocked reasons, and duplicate-write retry state. Handoff top-level safety and durability fields, including `task.external_write`, `task.requires_auth`, `task.draft_only`, `task.long_running`, `route.approval_required`, and `approval.required`, must use the same policy-derived values so stale stored plan flags cannot make the compact package appear safe or less durable. They must also expose `approval_integrity` so agents can see whether the latest pending, approved, or denied request still has an id, valid stage, fingerprints, and required decision metadata. Agents must inspect both before retrying or trusting a run. `approval_status=missing` means the plan required approval but the approval record is absent; `approval_integrity.status=mismatch` means the approval record no longer matches the current plan. Treat either as a policy bug and do not execute. Direct resume must record `resume_blocked` before any execution claim when approval integrity is `missing`, `mismatch`, `missing_fingerprint`, `missing_approval_id`, `missing_required_before`, `invalid_required_before`, `missing_decision_metadata`, or `unknown_status`.

Run reports must include `run_id` and `plan_sha256`, and verifier/handoff output must expose `run_id_integrity` and `plan_integrity`. Run ids must be safe generated `run_*` ids; dot-segment or otherwise invalid ids must be reported as `invalid_run_id` and must not define artifact roots. Ordinary terminal failed, blocked, or complete runs must have a readable `run-report.json`; if not, verifier must report `missing_run_report`. A `run-report.json` whose `run_id` does not match the saved run id must be reported as `run_report_run_id_mismatch`. Artifact paths are trusted only when they resolve inside `.super-browser/artifacts/<run-id>/` or the configured `SUPER_BROWSER_STATE_DIR` equivalent; verifier must not read or hash outside paths and must report `untrusted_artifact_path` instead. Missing artifact paths must be reported as `missing_artifact_path`, and changed artifact hashes must be reported as `artifact_hash_mismatch`. If multiple run-report artifacts exist, verifier must use the newest one. A successful resumed provider execution must replace stale execution artifact records while preserving the durable plan artifact and event history, so old failed report hashes cannot make the current run look corrupted. If the stored run id is invalid, if the stored payload is marked `store_payload_corrupt`, if the terminal run report is missing outside stale-recovery or retry-approval setup, if an artifact path is missing or hash-mismatched, if the report belongs to a different run id, if the stored run plan does not match the report fingerprint, if `run-report.json` `final_status` does not match the saved run status outside an approved external-write retry transition, if the final provider or attempt history is inconsistent with the stored provider sequence, if artifact paths are outside the run's artifact directory, or if verifier reports a provider sequence constraint failure, treat the run as untrusted evidence. Handoff must mark resume unsafe when `run_id_integrity` is invalid, when `plan_integrity` is `mismatch` or `missing`, when verifier failures include `invalid_run_id`, `store_payload_corrupt`, `missing_run_report`, `missing_artifact_path`, `artifact_hash_mismatch`, `run_report_run_id_mismatch`, `untrusted_artifact_path`, `status_mismatch`, `run_report_final_provider_not_planned`, `run_report_complete_without_complete_attempt`, `run_report_final_provider_attempt_mismatch`, `run_report_final_provider_attempt_missing`, or `run_report_final_status_attempt_mismatch` outside the explicit stale-recovery and retry-approval transitions, or when provider constraints fail; direct resume must record `resume_blocked` before any provider retry. Do not retry it as a provider failure; inspect the run store and artifacts or create a new run.

The low-level adapter API must not be an escape hatch. `execute_plan()` re-checks task policy, task payload validity, URL-derived target scope, and provider sequence constraints, blocks approval-gated plans by default, and blocks stale or hand-built plans that smuggle malformed constraints, embed URL credentials, downgrade sensitive target scopes, widen allowlists, route local files away from Playwright, select URL-required primary providers without a starting URL, reference unknown providers, or exceed cost ceilings. Only the durable runtime should pass explicit `approval_context` after it has recorded an approval decision. That context must include an approved status, approval id, `required_before`, action fingerprint, decision metadata, and matching plan fingerprint; a bare approval boolean is not sufficient.

Provider adapter exceptions are execution evidence, not approval or policy bypasses. `execute_plan()` must redact the exception message, save `provider-exception.json`, mark that provider attempt `failed`, and continue only to providers already present in the approved/planned sequence. An adapter crash must not clear an approval gate, widen the fallback list, leak secrets, or leave the run without a final run report when the execution loop can still return a structured result.

Runtime execution exceptions after a durable claim are also execution evidence, not active workers. The runtime must redact the exception message, save `runtime-exception.json`, write a failed `run-report.json` with `run_id` and `plan_sha256`, clear the execution lease, and preserve external-write retry protection. A crash after an approved post/comment/message/form attempt must not be retried until a fresh `provider_retry` approval is recorded.

## Non-Negotiables

- Never request API keys or passwords in chat.
- Never put secrets in prompts, logs, screenshots, or traces when avoidable.
- Never auto-submit posts, comments, DMs, account changes, payments, or destructive actions by default.
- Do not retry an external write after a crash without deduplication and review.
