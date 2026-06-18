---
name: super-browser-planner
description: Build Super Saiyan Browser execution plans. Use when an agent needs to choose browser/computer automation providers, compare reliability, auth, anti-bot, cost, duration, and desktop requirements, consult provider specialist skills, and return a provider sequence with required env vars and verification steps.
---

# Super Saiyan Browser Planner

## Role

Act as the developer/planning agent. Produce a decision-complete implementation plan for browser or computer automation.

## Planning Loop

1. Run `super-browser plan --goal "<goal>"`.
2. Read the returned `council_report`.
3. Identify relevant provider specialists from `specialists_consulted`.
4. Confirm each specialist recommendation: `use me`, `use me only as fallback`, `do not use me`, or `not enough proof`.
5. Enforce any provider allowlist or `max_cost_usd`; impossible constraints must fail planning.
6. Run **3 to 5 deliberation loops** (direct=3, council=5). Read `council_report.review_loops`, `deliberation_complete`, `execution_pattern`, and `documented_recommendations`.
7. Do not execute until `deliberation_complete` is true.
8. Return a plan with provider order, exact missing env vars, approval gates, expected artifacts, and verification steps.

## Output Contract

Return:

- `goal`
- `risk`
- `primary_provider`
- `fallback_providers`
- `council_report.specialists_consulted`
- `council_report.review_loops`
- `council_report.planner_decision.max_cost_usd`
- `council_report.planner_decision.providers_allowed`
- `cost_estimate.selected_provider_floor_usd`
- `cost_estimate.fallback_floor_usd`
- `cost_estimate.worst_case_floor_usd`
- `cost_estimate.budget_status`
- `required_env`
- `approval_required`
- `execution_steps`
- `verification_steps`
- `known_failure_modes`

## References

- Read `../../references/provider-matrix.md`.
- Read `../../references/cost-model.md`.
- Read `../../references/routing-playbook.md`.
- Read `../../references/combo-playbook.md` and `../../references/providers/README.md`.
