---
name: super-browser-verifier
description: Verify Super Saiyan Browser plans and runs. Use when an agent needs to test browser automation outputs, inspect run artifacts, check provider traces, validate external write guardrails, identify bugs or holes, and produce a confidence-rated run report.
---

# Super Saiyan Browser Verifier

## Role

Prove the browser/computer workflow worked, or state exactly what is still unproven.

## Checks

1. Run `super-browser verify <run-id>` when a run exists.
2. Inspect the returned `verification-report.json` path.
3. Inspect `run-report.json`: final provider, final status, attempt order, blocked/failed reasons, and selected fallback.
4. Inspect artifacts: screenshot, DOM snapshot, extracted JSON, HAR/network log, provider trace, live URL, recording, or desktop screenshot.
5. Confirm the output matches the user's goal.
6. Confirm external writes stopped for approval unless explicitly allowlisted.
7. Inspect `plan_integrity`: the stored run plan must match `run-report.json` `plan_sha256`; treat mismatch as evidence corruption.
8. Inspect `policy_guard`: target scope, approval status, safety events, blocked reasons, and duplicate-write retry state. Treat `approval_status=missing` as a broken approval-gate record, not as permission to run.
9. Confirm `write_retry_guard` blocks duplicate external-write retry unless a fresh retry approval was granted.
10. Confirm `run-report.json` final provider and final status are consistent with the planned provider sequence and the recorded attempts. Treat impossible final-provider or attempt evidence as unsafe to resume, not as a normal provider failure. A complete run must have a matching completed attempt.
11. Confirm `run_id_integrity.status` is verified. Treat `invalid_run_id` as evidence corruption; do not use dot-segment or otherwise invalid ids to derive artifact roots.
12. Confirm `run-report.json` `run_id` matches the saved run id. Treat `run_report_run_id_mismatch` as evidence corruption; do not hand off or resume a copied report from another run.
13. When multiple run-report artifacts exist, inspect the newest one; after a safe provider retry, stale execution artifact records should be replaced while event history remains.
14. Confirm every artifact path resolves inside `.super-browser/artifacts/<run-id>/` or the configured `SUPER_BROWSER_STATE_DIR` equivalent. Treat `untrusted_artifact_path` as evidence corruption; do not read, hash, hand off, or resume from outside paths.
15. Treat explicit provider error payloads, failed statuses, unfinished statuses after polling, and `success=false` as failed attempts even when provider output was saved.
16. When checking provider readiness, trust only doctor-filtered `certified_workflow_classes` and `production_ready_scope`; classes listed in `ignored_unsupported_evidence_workflow_classes` or `ignored_provider_mismatch_evidence_workflow_classes` are not proof.
17. Reproduce with the cheapest reliable local fixture when possible; run `super-browser live-test --provider fixtures` for browser behavior regressions.
18. Mark confidence as high, medium, low, or blocked.

## Report Contract

The verifier report must include provider, cost band, cost estimate, budget status, trace links, artifacts, artifact hashes, run-id integrity, run-report run-id consistency, artifact path scope, missing or changed artifact failures, approval state, `plan_integrity`, `policy_guard`, `write_retry_guard`, attempts, checks, and confidence.

Verifier output is redacted by default. Treat `[REDACTED]` in trace URLs, headers, provider output, or error text as expected when the original value looked like a cookie, token, API key, password, client secret, or authorization header.

## Bug Loop

If verification fails, send the failure to the planner with:

- provider
- step
- observed failure
- expected behavior
- artifact path or trace URL
- recommended fallback

## References

- Read `../../references/live-test-matrix.md`.
- Read `../../references/security-and-approval-policy.md`.
