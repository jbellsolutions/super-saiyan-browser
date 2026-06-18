from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .costs import estimate_provider_cost
from .live_evidence import load_live_test_evidence
from .models import TaskSpec
from .providers import PROVIDERS

PROVIDER_ESCALATION_RANK: dict[str, int] = {
    "brightdata-serp": -2,
    "decodo-http": -1,
    "playwright": 1,
    "browser-use": 1,
    "brightdata-unlocker": 1,
    "brightdata-dataset": 1,
    "brightdata-browser": 2,
    "hyperbrowser": 2,
    "airtop": 2,
    "browserbase": 2,
    "steel": 3,
    "orgo": 4,
}

CLOUD_SCALE_PROVIDERS = frozenset({"hyperbrowser", "airtop", "browserbase"})
CDP_SESSION_PROVIDERS = frozenset({"steel", "browserbase"})
SCRAPE_API_PROVIDERS = frozenset({"hyperbrowser", "brightdata-unlocker"})
BRIGHTDATA_TERMS = ("bright data", "brightdata", "web unlocker", "serp api", "dataset extractor", "scraping browser")
BROWSERBASE_TERMS = (
    "browserbase",
    "stagehand",
    "model gateway",
    "hosted web agent",
    "browserbase functions",
    "byok",
)
SCRAPE_TERMS = ("scrape", "markdown output", "html output", "extract links", "async scrape job")
CDP_TERMS = ("playwright cdp", "cdp connect", "connect_over_cdp", "selenium", "click ", "fill form", "selector")
DEFAULT_LOOPS_DIRECT = 3
DEFAULT_LOOPS_COUNCIL = 5


@dataclass
class DeliberationResult:
    primary: str
    fallbacks: list[str]
    loops: list[dict[str, Any]]
    deliberation_complete: bool
    execution_pattern: str = "single"
    combo_steps: list[dict[str, Any]] = field(default_factory=list)
    documented_recommendations: list[dict[str, Any]] = field(default_factory=list)
    loop_count: int = 0


def deliberate(
    task: TaskSpec,
    initial_ranked: list[str],
    *,
    mode: str,
    missing_env: Callable[[list[str]], list[str]],
    deliberation_rounds: int | None = None,
) -> DeliberationResult:
    if not initial_ranked:
        return DeliberationResult(
            primary="",
            fallbacks=[],
            loops=[{"loop": 1, "focus": "classification", "findings": ["No capable providers after constraints."]}],
            deliberation_complete=False,
        )

    target_loops = deliberation_rounds or (DEFAULT_LOOPS_COUNCIL if mode == "council" else DEFAULT_LOOPS_DIRECT)
    target_loops = max(3, min(5, target_loops))
    primary = initial_ranked[0]
    fallbacks = [name for name in initial_ranked[1:6] if name != primary]
    loops: list[dict[str, Any]] = []
    execution_pattern = "single"
    combo_steps: list[dict[str, Any]] = []
    documented = _documented_provider_recommendations(task)

    # Loop 1 — classification
    loops.append(
        {
            "loop": 1,
            "focus": "classification",
            "findings": _classification_findings(task),
            "required_capabilities": _required_capabilities(task),
        }
    )

    # Loop 2 — single provider + redundancy filter
    fallbacks, redundancy_notes = _apply_redundancy_filter(task, primary, fallbacks)
    loops.append(
        {
            "loop": 2,
            "focus": "single_provider_redundancy",
            "findings": [
                f"Initial primary: {primary}.",
                f"Fallbacks after redundancy filter: {', '.join(fallbacks) if fallbacks else 'none'}.",
                *redundancy_notes,
            ],
            "primary": primary,
            "fallbacks": list(fallbacks),
        }
    )

    # Loop 3 — env readiness + live evidence
    primary, fallbacks, readiness_notes = _apply_readiness_adjustment(task, primary, fallbacks, missing_env)
    loops.append(
        {
            "loop": 3,
            "focus": "env_and_live_evidence",
            "findings": readiness_notes,
            "primary": primary,
            "fallbacks": list(fallbacks),
        }
    )

    if target_loops >= 4:
        primary, fallbacks, execution_pattern, combo_steps, combo_notes = _apply_shape_preference(
            task, primary, fallbacks
        )
        loops.append(
            {
                "loop": 4,
                "focus": "task_shape_and_combo",
                "findings": combo_notes,
                "execution_pattern": execution_pattern,
                "primary": primary,
                "fallbacks": list(fallbacks),
                "combo_steps": list(combo_steps),
            }
        )

    if target_loops >= 5:
        primary, fallbacks, simplicity_notes = _apply_simplicity_challenge(task, primary, fallbacks)
        loops.append(
            {
                "loop": 5,
                "focus": "simplest_tool_wins",
                "findings": simplicity_notes,
                "primary": primary,
                "fallbacks": list(fallbacks),
            }
        )

    deliberation_complete = len(loops) >= target_loops and bool(primary)
    if documented and not any(name in [primary, *fallbacks] for name in ("browserbase",)):
        loops.append(
            {
                "loop": len(loops) + 1,
                "focus": "documented_provider_note",
                "findings": [
                    "Task signals a documented-only provider; Super Saiyan Browser has no live adapter yet.",
                    documented[0].get("reason", ""),
                ],
            }
        )

    return DeliberationResult(
        primary=primary,
        fallbacks=fallbacks,
        loops=loops,
        deliberation_complete=deliberation_complete,
        execution_pattern=execution_pattern,
        combo_steps=combo_steps,
        documented_recommendations=documented,
        loop_count=len(loops),
    )


def _classification_findings(task: TaskSpec) -> list[str]:
    findings = [f"Goal classified with optimize={task.optimize}."]
    if task.raw_http:
        findings.append("Raw HTTP lane — browser providers excluded.")
    if task.needs_desktop:
        findings.append("Desktop/computer surface required.")
    if task.anti_bot_risk:
        findings.append("Anti-bot risk flagged — prefer hardened cloud browsers.")
    if task.requires_auth:
        findings.append("Authenticated or profile-bearing session likely required.")
    if task.external_write:
        findings.append("External write detected — approval gate applies.")
    if task.target_scope != "public_web":
        findings.append(f"Target scope: {task.target_scope}.")
    return findings


def _required_capabilities(task: TaskSpec) -> list[str]:
    caps: list[str] = []
    if task.raw_http:
        caps.append("supports_raw_http")
    if task.needs_desktop:
        caps.append("supports_desktop")
    if task.anti_bot_risk:
        caps.append("supports_anti_bot")
    if task.requires_auth:
        caps.append("supports_auth")
    if task.long_running:
        caps.append("supports_long_running")
    if task.profile:
        caps.append("supports_profiles")
    if task.proxy:
        caps.append("supports_proxy_injection")
    return caps


def _apply_redundancy_filter(
    task: TaskSpec,
    primary: str,
    fallbacks: list[str],
) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    if task.raw_http or task.needs_desktop:
        return fallbacks, notes

    filtered: list[str] = []
    primary_rank = PROVIDER_ESCALATION_RANK.get(primary, 99)
    for name in fallbacks:
        if name == primary:
            continue
        rank = PROVIDER_ESCALATION_RANK.get(name, 99)
        if (
            primary in CLOUD_SCALE_PROVIDERS
            and name in CLOUD_SCALE_PROVIDERS
            and primary_rank == rank
            and not _needs_distinct_cloud_providers(task, primary, name)
        ):
            notes.append(f"Dropped redundant cloud-scale fallback {name}; {primary} already covers this tier.")
            continue
        if primary in SCRAPE_API_PROVIDERS and name in CDP_SESSION_PROVIDERS and _scrape_prefers_hyperbrowser(task):
            notes.append(f"Kept {name} as CDP fallback only if scrape job fails — prefer single-provider {primary} first.")
        filtered.append(name)
    if not notes:
        notes.append("No redundant multi-cloud fallbacks removed.")
    return filtered, notes


def _needs_distinct_cloud_providers(task: TaskSpec, a: str, b: str) -> bool:
    if _scrape_prefers_hyperbrowser(task) and {a, b} <= (SCRAPE_API_PROVIDERS | CDP_SESSION_PROVIDERS):
        return a in SCRAPE_API_PROVIDERS and b in CDP_SESSION_PROVIDERS
    return False


def _scrape_prefers_hyperbrowser(task: TaskSpec) -> bool:
    text = task.goal.lower()
    return task.raw_http or any(term in text for term in SCRAPE_TERMS)


def _cdp_prefers_steel(task: TaskSpec) -> bool:
    text = task.goal.lower()
    return any(term in text for term in CDP_TERMS) or (task.profile and "steel" in text)


def _apply_readiness_adjustment(
    task: TaskSpec,
    primary: str,
    fallbacks: list[str],
    missing_env: Callable[[list[str]], list[str]],
) -> tuple[str, list[str], list[str]]:
    notes: list[str] = []
    sequence = [primary, *fallbacks]
    missing = missing_env(sequence)
    if missing and primary in sequence:
        primary_missing = missing_env([primary])
        if primary_missing:
            for candidate in fallbacks:
                if not missing_env([candidate]):
                    notes.append(
                        f"Promoted {candidate} to primary because {primary} is missing env: {', '.join(primary_missing)}."
                    )
                    new_fallbacks = [name for name in fallbacks if name != candidate]
                    new_fallbacks.insert(0, primary)
                    return candidate, new_fallbacks, notes
            notes.append(f"Primary {primary} missing env {', '.join(primary_missing)}; plan remains plan-only until configured.")

    evidence = load_live_test_evidence(primary)
    if PROVIDERS.get(primary) and PROVIDERS[primary].stability == "evaluating" and not evidence:
        notes.append(f"{primary} is evaluating with no fresh live-test evidence for this workflow class.")

    if not notes:
        notes.append("Env readiness and live-evidence checks passed for the selected sequence.")
    return primary, fallbacks, notes


def _apply_shape_preference(
    task: TaskSpec,
    primary: str,
    fallbacks: list[str],
) -> tuple[str, list[str], str, list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    combo_steps: list[dict[str, Any]] = []
    execution_pattern = "single"
    text = task.goal.lower()

    if _scrape_prefers_hyperbrowser(task) and "hyperbrowser" in [primary, *fallbacks]:
        if primary != "hyperbrowser" and "hyperbrowser" in fallbacks:
            notes.append("Scrape-shaped task — hyperbrowser is the preferred single provider when credentials exist.")
            fallbacks = [name for name in fallbacks if name != "hyperbrowser"]
            fallbacks.insert(0, primary)
            primary = "hyperbrowser"
        else:
            notes.append("Scrape-shaped task — keeping hyperbrowser as primary.")

    elif _cdp_prefers_steel(task) and "steel" in [primary, *fallbacks]:
        if primary != "steel":
            notes.append("CDP/automation-shaped task — steel is preferred over scrape APIs when credentials exist.")
            if "steel" in fallbacks:
                fallbacks = [name for name in fallbacks if name != "steel"]
                fallbacks.insert(0, primary)
                primary = "steel"

    if re.search(r"\bsteel\b.*\b(browserbase|computer use|computer-use)\b", text) or re.search(
        r"\b(browserbase|computer use|computer-use)\b.*\bsteel\b", text
    ):
        execution_pattern = "combo"
        combo_steps = [
            {
                "order": 1,
                "provider": "steel",
                "purpose": "Host Chromium session (CDP or Selenium) for computer-use loop.",
            },
            {
                "order": 2,
                "provider": "documented",
                "purpose": "Drive actions with external computer-use agent + BYOK LLM; see references/combo-playbook.md.",
            },
        ]
        notes.append("Combo pattern documented — runtime executes single-provider unless multi-step adapter exists.")

    if not notes:
        notes.append("No task-shape override; keeping ranked primary.")
    return primary, fallbacks, execution_pattern, combo_steps, notes


def _apply_simplicity_challenge(
    task: TaskSpec,
    primary: str,
    fallbacks: list[str],
) -> tuple[str, list[str], list[str]]:
    notes: list[str] = []
    if task.needs_desktop or task.raw_http:
        notes.append("Simplicity check skipped — hard capability filter already applied.")
        return primary, fallbacks, notes

    provider = PROVIDERS.get(primary)
    if provider and provider.cost_band == "free" and not task.anti_bot_risk and not task.requires_auth:
        notes.append(f"Keeping free local provider {primary} — simplest tool that satisfies the task.")
        return primary, fallbacks, notes

    if task.anti_bot_risk and primary != "browser-use" and "browser-use" in fallbacks:
        if not os.environ.get("BROWSER_USE_API_KEY"):
            notes.append("Anti-bot task but BROWSER_USE_API_KEY unset — cannot promote browser-use.")
        else:
            notes.append("Anti-bot task — browser-use is the simplest hardened default when credentialed.")
            fallbacks = [name for name in fallbacks if name != "browser-use"]
            fallbacks.insert(0, primary)
            primary = "browser-use"

    # Prefer single cloud provider: trim extra rank-2 fallbacks when primary already cloud-capable
    if primary in CLOUD_SCALE_PROVIDERS | CDP_SESSION_PROVIDERS | {"browser-use"}:
        trimmed = []
        for name in fallbacks:
            if name in CLOUD_SCALE_PROVIDERS and primary in CLOUD_SCALE_PROVIDERS:
                continue
            trimmed.append(name)
        if len(trimmed) < len(fallbacks):
            notes.append("Trimmed extra cloud-scale fallbacks to avoid using multiple clouds for one job.")
            fallbacks = trimmed

    if not notes:
        notes.append("Simplicity check confirms current primary and fallback ladder.")
    return primary, fallbacks, notes


def _documented_provider_recommendations(task: TaskSpec) -> list[dict[str, Any]]:
    text = task.goal.lower()
    if not any(term in text for term in BROWSERBASE_TERMS):
        return []
    provider = PROVIDERS.get("browserbase")
    if not provider or provider.stability != "docs-only":
        return []
    return [
        {
            "provider": "browserbase",
            "status": "docs-only",
            "reason": "Task mentions Browserbase/Stagehand/hosted-agent surfaces; adapter not wired — see references/providers/browserbase.md.",
            "required_env": ["BROWSERBASE_API_KEY"],
            "missing_env": [] if os.environ.get("BROWSERBASE_API_KEY") else ["BROWSERBASE_API_KEY"],
            "docs_url": provider.docs_url,
            "signup_url": "https://www.browserbase.com/",
        }
    ]


def deliberate_weekly_intelligence(
    provider_name: str,
    *,
    current_summary: str,
    fetched_summary: str,
) -> dict[str, Any]:
    """Lightweight deliberation for weekly doc sync — routing relevance only."""
    changed = current_summary.strip() != fetched_summary.strip()
    loops = [
        {
            "loop": 1,
            "focus": "diff_detected",
            "findings": [f"Provider {provider_name}: content changed={changed}."],
        },
        {
            "loop": 2,
            "focus": "routing_relevance",
            "findings": [
                "Apply updates only when capability flags, env vars, or routing playbook rows change.",
                "Skip cosmetic doc edits.",
            ],
        },
        {
            "loop": 3,
            "focus": "simplest_update",
            "findings": [
                "Update references/providers SSOT first, then specialist skills, then providers.py flags.",
            ],
        },
    ]
    return {
        "provider": provider_name,
        "apply": changed,
        "deliberation_complete": True,
        "review_loops": loops,
        "verdict": "apply" if changed else "no_op",
    }
