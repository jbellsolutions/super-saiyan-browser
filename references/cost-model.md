# Super Saiyan Browser Cost Model

Use this model to keep routing practical. Exact prices change, so treat values as planning bands unless verified on the provider billing page.

| Cost band | Meaning | Providers |
| --- | --- | --- |
| free | Local or already bundled | Playwright |
| low | Cheap per request/GB when rendering is unnecessary and an HTTP endpoint is supplied | Decodo HTTP |
| medium | Subscription or per-machine cost that should be justified | Orgo, Airtop |
| variable | Session/token/proxy/credit costs can move fast | Browser Use, Hyperbrowser, Steel |

## Rules

- Cheapest reliable tool wins, not cheapest theoretical tool.
- `--allow-provider` is a strict allowlist; unlisted providers must not be selected.
- `--max-cost-usd` filters providers by the cost-floor table below. If none fit, planning fails. Verifier, handoff, direct resume, and low-level execution re-check the stored provider sequence against the same ceiling before provider dispatch.
- If anti-bot risk is high, do not burn time and account reputation by repeatedly trying weak providers.
- If the page is a raw API/JSON endpoint, do not launch a browser.
- If a workflow needs a logged-in user profile, cost includes human reauth friction.
- If a provider has no fresh live test for the task class in doctor-filtered `certified_workflow_classes`, mark it evaluating for that class and do not call it production-ready for that workflow. Classes listed in `ignored_unsupported_evidence_workflow_classes` or `ignored_provider_mismatch_evidence_workflow_classes` do not count as proof.
- For long-running jobs, include browser session time, proxy bandwidth, LLM/tool credits, retries, and verification artifacts.

## Cost Floors Used By Router

These are conservative routing floors, not provider billing promises:

| Cost band | Floor |
| --- | --- |
| free | `0.0` |
| low | `0.01` |
| medium | `0.25` |
| variable | `0.05` |
| high | `1.0` |

## Cost Estimate Fields

`super-browser plan`, `run-report.json`, and `super-browser verify <run-id>` include a `cost_estimate` object:

- `primary`: selected provider cost band, floor, multiplier, confidence, and notes.
- `fallbacks`: planned fallback provider estimates.
- `selected_provider_floor_usd`: floor for the selected provider.
- `fallback_floor_usd`: sum of fallback floors.
- `worst_case_floor_usd`: selected provider plus fallbacks if every attempt runs once.
- `budget_status`: `no_ceiling`, `within_ceiling`, or `over_ceiling`.
- `max_cost_usd`: the user-supplied cost ceiling, if any.

Long-running, anti-bot, desktop, and authenticated workflows can add conservative multipliers or notes. These estimates are meant for routing, plan review, and verifier reporting; live provider billing must still be checked against provider dashboards for production budgeting.

## Cost-Sensitive Provider Order

1. Playwright or direct local tooling.
2. Decodo/raw HTTP when rendering is unnecessary and the task supplies an HTTP endpoint.
3. Browser Use for hard anti-bot or complex cloud agent work.
4. Hyperbrowser or Airtop for general cloud sessions at scale.
5. Steel for hosted Chromium sessions when CDP control is needed.
6. Orgo only when desktop/computer use is actually required.
7. Hyperbrowser, Airtop, and Steel only count as production-ready after live tests prove the task class.
