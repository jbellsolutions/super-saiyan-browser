# Live Test Matrix

Live tests are gated by env vars. If a key is missing, the test should skip and report which env var is required.

| Scenario | Local fixture | Live provider tests | Required proof |
| --- | --- | --- | --- |
| Basic extraction | Playwright fixture page | Browser Use, Hyperbrowser, Steel | Structured JSON matches schema |
| Login/session | Local fake login | Browser Use profiles, Airtop sessions | Session persists across run |
| Infinite scroll | Fixture list page | Browser Use, Hyperbrowser | Expected item count |
| Form fill no submit | Fixture form | Playwright, Browser Use | Draft exists, not submitted |
| External write gate | Fixture submit page | All write-capable routes | Run stops awaiting approval |
| Raw HTTP | Local JSON endpoint | Decodo direct raw HTTP, plus proxy metadata when `DECODO_PROXY` exists | Status, body, direct/proxy metadata |
| Full desktop | None by default | Orgo | Computer-use response, screenshot, cleanup |
| Anti-bot | No public automated target by default | Browser Use first, others evaluating | Trace plus explicit status |

## Built-In Command

Run local proof without paid provider keys:

```bash
super-browser live-test --provider local
```

This exercises:

- `decodo-http` adapter against a local JSON fixture without proxy.
- `playwright` adapter against a local HTML fixture with screenshot, text, and metadata artifacts.
- the fixture matrix: login/session, infinite scroll, form fill without submit, social feed scan/comment draft without publish, lead-generation extraction to local artifact without CRM/email, modal handling, file upload, blocked page detection, normal long-running resume, and stale execution lease recovery.

Run only the fixture matrix:

```bash
super-browser live-test --provider fixtures
```

The fixture matrix uses a local HTTP server and local Playwright. It does not use paid provider keys. It covers login/session state, infinite scroll, draft-only form fill, social feed scanning with high-intent post matching and comment drafting without publishing, lead-generation extraction to a local artifact while avoiding CRM sync and email actions, modal handling, file upload, blocked pages, normal resume, and stale execution recovery.

Run all configured provider proofs:

```bash
super-browser live-test --provider all
super-browser live-test --provider decodo-http --workflow-class raw_http_direct
super-browser live-test --provider browser-use --workflow-class external_write_gate
```

Providers with missing keys return `skipped`. A skipped result is an observation that no live provider execution happened; it does not erase a previous fresh pass for the same workflow class. A real `failed` result does replace the per-class record and removes certification for that class. Providers with configured keys run through the durable saved-run lifecycle with a strict single-provider allowlist, then execute through `resume` or the standard approval flow when the fixture is policy-gated. This keeps live evidence tied to the same runtime path agents use. Provider fixtures run a read-only `https://example.com` task, except Orgo, which submits a harmless computer-use task and requests a screenshot against the resolved computer (pinned `ORGO_COMPUTER_ID`, or an auto-discovered/created computer in the `super-browser` workspace).

Each provider evidence record includes a `workflow_class`. Current built-in classes are `raw_http_direct`, `local_browser_fixture`, `general_read`, `authenticated_read`, `desktop_read`, and `external_write_gate`. Doctor exposes `certified_workflow_classes` and `production_ready_scope`; agents must not treat a `general_read` pass as proof for social posting, anti-bot, authenticated, or desktop workflows. Evidence accumulates by workflow class per provider, so running a second class does not erase a still-fresh earlier class proof.

Doctor filters persisted evidence against the provider's supported workflow-class list and embedded provider identity before setting `readiness_status`, `certified_workflow_classes`, or `production_ready_scope`. If a stale, hand-built, or incompatible evidence file claims an unsupported class, that class is listed in `ignored_unsupported_evidence_workflow_classes`. If the embedded evidence provider does not match the provider being certified, that class is listed in `ignored_provider_mismatch_evidence_workflow_classes`. Neither field certifies the provider.

Supported built-in provider/class pairs:

| Provider | Built-in workflow class |
| --- | --- |
| `decodo-http` | `raw_http_direct` |
| `playwright` | `local_browser_fixture`, `external_write_gate` |
| `orgo` | `desktop_read`, `external_write_gate` |
| `browser-use`, `airtop`, `hyperbrowser`, `steel` | `general_read`, `external_write_gate` |

`external_write_gate` creates a provider-locked publish/comment-style run and passes only when the run stops in `awaiting_approval`, a pending approval exists, and no provider execution event starts. It can run without provider credentials because it must not call the provider.

When a requested provider/class pair is not supported, the live-test command returns `failed` with `unsupported_workflow_class=true` and does not overwrite existing evidence.

## Doctor Readiness Statuses

`super-browser doctor` separates provider setup from production confidence:

| Status | Meaning |
| --- | --- |
| `ready_local` | Local provider is usable and covered by fixture verification. |
| `live_test_passed` | Fresh persisted live-test evidence exists for the listed workflow class. |
| `live_test_stale` | A previous provider live test passed, but the evidence is older than the freshness window. |
| `missing_env` | Required env vars are missing; do not attempt the provider. |
| `package_missing` | Credentials may exist, but the required local package or CLI is unavailable. |
| `runtime_missing` | The package is installed, but the local browser runtime cannot launch. For Playwright, run `playwright install chromium`. |
| `usable_direct_http_no_proxy` | Direct raw HTTP can run now for supplied HTTP endpoints, but residential proxy routing is not configured. |
| `configured_live_test_recommended` | Stable provider appears configured; run its live test before production use. |
| `configured_live_test_required` | Evaluating provider appears configured; live test evidence is required before production use. |

Use `usable_now` to decide whether an adapter can be attempted. Use `production_ready_scope`, `certified_workflow_classes`, `stale_certified_workflow_classes`, `supported_live_workflow_classes`, `uncertified_workflow_classes`, `ignored_unsupported_evidence_workflow_classes`, `ignored_provider_mismatch_evidence_workflow_classes`, `requires_live_test_before_production`, `requires_live_test_before_broader_production`, and `production_blockers` to decide whether it can be treated as proven for a production workflow class. Paid/live providers should not be trusted for a workflow class until current live-test artifacts prove that class and doctor accepts that class for the provider.

Run `super-browser production-readiness` or MCP `production_readiness` as the final go-live gate. The CLI exits `0` only when every required provider is production-ready for its supported workflow classes. It exits `1` with a JSON `status=blocked` report when env vars are missing, workflow classes are uncertified, evidence is stale, or provider-mismatched/unsupported evidence was ignored. Use repeated `--require-provider <name>` only for intentionally smaller deployments.

Live-test summaries are written to the Super Saiyan Browser state directory under `live-tests/<provider>.json`. They contain redacted provider, status, timestamp, run id, workflow class, certified workflow classes, per-class latest records, checks, artifact count, event count, and confidence metadata. Set `SUPER_BROWSER_LIVE_EVIDENCE_MAX_AGE_DAYS` to change the default 30-day freshness window. Set `SUPER_BROWSER_RECORD_LIVE_TEST_EVIDENCE=0` to disable evidence writes for one-off CI runs.

When run through `./scripts/verify-super-browser`, live-test evidence uses a temporary `SUPER_BROWSER_STATE_DIR` and Python cache prefix unless overridden with `SUPER_BROWSER_VERIFY_STATE_DIR` or `SUPER_BROWSER_VERIFY_PYCACHE_DIR`. Direct `super-browser live-test` commands still write to the configured durable state directory.

## Live-Gated Adapter Commands

Browser Use:

```bash
export BROWSER_USE_API_KEY=...
super-browser run --goal "Search a protected public site and return JSON" --url "https://example.com"
```

Orgo:

```bash
export ORGO_API_KEY=...
# Optional: pin a specific computer; otherwise one is discovered or created automatically.
# export ORGO_COMPUTER_ID=...
super-browser run --goal "Use a desktop computer to inspect files"
```

The Orgo adapter intentionally requires an existing computer ID. It does not create paid computers automatically.

Airtop:

```bash
export AIRTOP_API_KEY=...
super-browser run --goal "Extract a page summary with Airtop page-query" --url "https://example.com"
```

The Airtop adapter creates a session, opens a window, queries the page, then terminates the session.

Hyperbrowser:

```bash
export HYPERBROWSER_API_KEY=...
super-browser run --goal "Scrape markdown, HTML, and links with Hyperbrowser" --url "https://example.com"
```

The Hyperbrowser adapter calls REST `/scrape`, polls `/scrape/{jobId}/status`, and fetches `/scrape/{jobId}` only after a completed status.

Steel:

```bash
python3 -m pip install playwright
export STEEL_API_KEY=...
super-browser run --goal "Capture page text and screenshot with Steel" --url "https://example.com"
```

The Steel adapter connects over Playwright CDP and captures the same artifact types as local Playwright.

## Confidence Levels

- High: target behavior verified with artifacts from the actual provider.
- Medium: plan and local fixture pass, but provider live key is missing.
- Low: static plan only.
- Blocked: provider failed or required setup is missing.
