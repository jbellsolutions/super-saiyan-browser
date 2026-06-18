from __future__ import annotations

import os
import re
import math
import socket
from ipaddress import ip_address
from numbers import Real
from typing import Any
from urllib.parse import urlparse

from .costs import estimate_provider_cost, estimate_sequence_cost
from .deliberation import deliberate
from .models import Plan, PlanStep, TargetScope, TaskSpec
from .policy import approval_required, enrich_policy_flags, infer_risk, long_running_for_goal, requires_auth_for_goal
from .profiles import ProfileStore
from .providers import PROVIDERS
from .brightdata.datasets import dataset_tool_for_url

# Escalation ladder: a cost/escalation tie-breaker, not the routing model.
# Capabilities (auth, anti-bot, captcha, profiles, proxy, fleet, desktop, raw HTTP)
# decide which providers CAN do the job; the rank only orders equally capable
# providers from cheapest/most deterministic to most expensive.
# Rank -1 = raw HTTP lane only (decodo), never used for browser work.
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

ESCALATION_PRIORITY_BONUS: dict[int, int] = {-2: 0, -1: 0, 1: 40, 2: 30, 3: 20, 4: 10}

OPTIMIZE_VALUES = ("balanced", "cost", "reliability")
ANTI_BOT_TERMS = ("cloudflare", "perimeterx", "datadome", "facebook", "instagram", "linkedin", "meta", "captcha")
AUTH_TERMS = (
    "login",
    "log in",
    "logged in",
    "logged-in",
    "logged into",
    "sign in",
    "signed in",
    "signed-in",
    "authenticated",
    "authentication",
    "auth",
    "credentials",
    "passkey",
    "session",
    "cookies",
    "cookie",
    "oauth",
    "token",
    "chrome profile",
    "browser profile",
    "local profile",
    "user profile",
    "my profile",
    "existing profile",
    "my account",
    "account settings",
    "account profile",
    "private",
    "member-only",
    "2fa",
    "otp",
)
DESKTOP_TERMS = ("desktop", "computer", "vm", "install app", "multi-window", "file system", "terminal")
RAW_HTTP_TERMS = ("api endpoint", "json endpoint", "curl", "raw http", "requests", "http api")
SERP_TERMS = ("google search", "bing search", "serp", "search results", "rankings for", "search for", "google dork")
INTERACTION_TERMS = ("click ", "fill form", "paginate", "pagination", "scroll ", "login", "sign in", "submit form")
STRUCTURED_EXTRACT_TERMS = ("company profile", "linkedin profile", "extract profile", "structured data", "dataset", "search dataset")
ALLOWED_URL_SCHEMES = ("http", "https", "file")
RAW_HTTP_URL_SCHEMES = ("http", "https")
LOCAL_HOSTNAMES = {"localhost", "localhost.localdomain"}
PRIVATE_HOST_SUFFIXES = (".local", ".internal", ".lan", ".home", ".corp")
TRAILING_EXTRACTED_URL_CHARS = ".,;:!?)]}>\"'"
URL_REQUIRED_PROVIDERS = {
    "playwright",
    "decodo-http",
    "airtop",
    "hyperbrowser",
    "steel",
    "brightdata-unlocker",
    "brightdata-dataset",
    "brightdata-browser",
}


def infer_task(
    goal: str,
    url: str | None = None,
    optimize: str = "balanced",
    providers_allowed: list[str] | None = None,
    max_cost_usd: float | None = None,
    timeout_seconds: int | None = None,
    profile: str | None = None,
    proxy: str | None = None,
    fleet_index: int | None = None,
) -> TaskSpec:
    goal = _validate_goal(goal)
    text = goal.lower()
    task_url = _validate_url(url or _extract_url(goal))
    allowed_providers = _validate_providers_allowed(providers_allowed)
    optimize_value = _validate_optimize(optimize)
    max_cost = _validate_max_cost_usd(max_cost_usd)
    timeout = _validate_timeout_seconds(timeout_seconds)
    profile_name = _validate_profile_name(profile)
    proxy_value = _validate_proxy_hint(proxy)
    if profile_name:
        _ensure_profile_exists(profile_name)
    serp_query = _infer_serp_query(goal, task_url)
    structured_extract = _infer_structured_extract(goal, task_url)
    task = TaskSpec(
        goal=goal,
        url=task_url,
        anti_bot_risk=_contains_any(text, ANTI_BOT_TERMS) or _contains_any(text, INTERACTION_TERMS),
        requires_auth=requires_auth_for_goal(goal) or bool(profile_name),
        needs_desktop=_contains_any(text, DESKTOP_TERMS),
        raw_http=_contains_any(text, RAW_HTTP_TERMS),
        serp_query=serp_query,
        serp_engine=_infer_serp_engine(text),
        serp_geo=_infer_serp_geo(text),
        structured_extract=structured_extract,
        long_running=long_running_for_goal(goal),
        target_scope=_target_scope_for_url(task_url),
        optimize=optimize_value,
        max_cost_usd=max_cost,
        timeout_seconds=timeout,
        providers_allowed=allowed_providers,
        profile=profile_name,
        proxy=proxy_value,
        fleet_index=fleet_index,
    )
    return enrich_policy_flags(task)


def build_plan(task: TaskSpec, *, deliberation_rounds: int | None = None) -> Plan:
    _validate_task_constraints(task)
    if task.profile:
        _ensure_profile_exists(task.profile)
    _validate_planning_requirements(task)
    ranked = rank_providers(task)
    if not ranked:
        raise ValueError(_constraint_error(task))
    mode = "council" if _needs_council(task) else "direct"
    deliberation = deliberate(
        task,
        ranked,
        mode=mode,
        missing_env=_missing_env,
        deliberation_rounds=deliberation_rounds,
    )
    primary = deliberation.primary or ranked[0]
    fallbacks = deliberation.fallbacks or ranked[1:5]
    missing_env = _missing_env([primary] + fallbacks)
    risk = infer_risk(task.goal)
    cost_estimate = estimate_sequence_cost([primary, *fallbacks], task)
    steps = [
        PlanStep(
            order=1,
            provider="super-browser-planner",
            purpose="Classify the task, cost, auth, risk, and provider constraints.",
            risk="read",
        ),
        PlanStep(
            order=2,
            provider=primary,
            purpose=_purpose_for(primary, task),
            risk=risk,
            required_env=_required_env_for(primary),
        ),
        PlanStep(
            order=3,
            provider="super-browser-verifier",
            purpose="Review artifacts, trace links, extracted data, and policy state before final answer.",
            risk="read",
        ),
    ]
    if approval_required(task):
        steps.insert(
            2,
            PlanStep(
                order=3,
                provider="publishing-safety-specialist",
                purpose="Gate any external write, credential use, publish, submit, or destructive action before execution.",
                risk=risk,
            ),
        )
        for idx, step in enumerate(steps, start=1):
            step.order = idx
    return Plan(
        task=task,
        mode=mode,
        primary_provider=primary,
        fallback_providers=fallbacks,
        steps=steps,
        missing_env=missing_env,
        approval_required=approval_required(task),
        rationale=_rationale(task, primary, mode),
        council_report=_council_report(
            task,
            ranked,
            primary,
            fallbacks,
            missing_env,
            mode,
            cost_estimate,
            deliberation=deliberation,
        ),
        cost_estimate=cost_estimate,
    )


def rank_providers(task: TaskSpec) -> list[str]:
    scores: dict[str, int] = {}
    candidates = _candidate_providers(task)
    for name in candidates:
        provider = PROVIDERS[name]
        score = 0
        rank = PROVIDER_ESCALATION_RANK.get(name, 99)
        if rank >= 1:
            score += ESCALATION_PRIORITY_BONUS.get(rank, 0)
        if name == "playwright" and not task.anti_bot_risk and not task.requires_auth:
            score += 50
        if task.optimize == "cost":
            score += {"free": 25, "low": 18, "medium": 8, "variable": 5, "high": 0}[provider.cost_band]
        if task.optimize == "reliability":
            score += {"stable": 25, "evaluating": 8, "docs-only": 0}[provider.stability]
        if task.raw_http:
            score += 70 if provider.supports_raw_http else -20
        if task.serp_query:
            score += 90 if provider.supports_serp else -40
        if task.structured_extract:
            score += 85 if provider.supports_structured_extract else -20
        if task.anti_bot_risk and provider.supports_unlocked_http and not task.external_write:
            score += 35
        if task.optimize == "cost" and provider.supports_unlocked_http:
            score += 12
        if _contains_any(task.goal.lower(), INTERACTION_TERMS) and name == "brightdata-browser":
            score += 25
        if task.needs_desktop:
            score += 80 if provider.supports_desktop else -35
        if task.anti_bot_risk:
            score += 75 if provider.supports_anti_bot else -10
        if task.requires_auth:
            score += 45 if provider.supports_auth else -15
            if name == "browser-use":
                score += 10
        if task.long_running:
            score += 20 if provider.supports_long_running else -8
        if task.external_write and name in ("browser-use", "orgo"):
            score += 8
        if task.external_write and not task.url and name in URL_REQUIRED_PROVIDERS:
            score -= 70
        if task.profile:
            score += 60 if provider.supports_profiles else -40
            if task.profile and name == "browser-use":
                score += 8
        if task.proxy and provider.supports_proxy_injection:
            score += 25
        if provider.stability == "evaluating":
            score -= 12
        scores[name] = score
    return sorted(
        scores,
        key=lambda item: (-scores[item], PROVIDER_ESCALATION_RANK.get(item, 99)),
    )


def _candidate_providers(task: TaskSpec) -> list[str]:
    candidates = [name for name in PROVIDERS if not task.providers_allowed or name in task.providers_allowed]
    if task.serp_query:
        candidates = [name for name in candidates if name == "brightdata-serp"]
    elif task.raw_http:
        candidates = [name for name in candidates if name == "decodo-http"]
    else:
        candidates = [name for name in candidates if name not in {"decodo-http", "brightdata-serp"}]
    if task.structured_extract and task.url:
        candidates = [name for name in candidates if name in {"brightdata-dataset", "brightdata-unlocker", "brightdata-browser", "browser-use", "hyperbrowser", "playwright", "steel"}]
    if not task.url and not task.serp_query and not task.dataset_filter:
        candidates = [name for name in candidates if name not in URL_REQUIRED_PROVIDERS]
    if _is_file_url(task.url):
        candidates = [name for name in candidates if name == "playwright"]
    if task.max_cost_usd is not None:
        candidates = [name for name in candidates if estimate_provider_cost(name, task)["estimated_floor_usd"] <= task.max_cost_usd]
    if task.profile:
        candidates = [name for name in candidates if PROVIDERS[name].supports_profiles]
    if task.proxy:
        candidates = [name for name in candidates if PROVIDERS[name].supports_proxy_injection or name == "decodo-http"]
    candidates = [name for name in candidates if PROVIDERS[name].stability != "docs-only"]
    return candidates


def _constraint_error(task: TaskSpec) -> str:
    constraints = []
    if task.providers_allowed:
        constraints.append(f"allowed providers: {', '.join(task.providers_allowed)}")
    if task.max_cost_usd is not None:
        constraints.append(f"max_cost_usd: {task.max_cost_usd}")
    return "No Super Saiyan Browser provider satisfies the routing constraints" + (f" ({'; '.join(constraints)})" if constraints else "")


def provider_sequence_constraint_failures(plan: Plan | dict[str, Any]) -> list[dict[str, Any]]:
    declared_target_scope = _declared_target_scope(plan)
    task = _task_from_plan(plan)
    if task is None:
        return [{"type": "provider_constraint_invalid_task", "message": "Plan task payload is invalid"}]
    sequence = _plan_provider_sequence(plan)
    failures: list[dict[str, Any]] = []
    if declared_target_scope != task.target_scope:
        failures.append(
            {
                "type": "provider_target_scope_mismatch",
                "message": "Plan task target_scope does not match the URL-derived target scope",
                "declared_target_scope": declared_target_scope,
                "derived_target_scope": task.target_scope,
                "url": task.url,
            }
        )
    unknown = [name for name in sequence if name not in PROVIDERS]
    if unknown:
        failures.append(
            {
                "type": "provider_constraint_unknown_provider",
                "message": "Plan provider sequence references unknown providers",
                "providers": unknown,
            }
        )
    known_sequence = [name for name in sequence if name in PROVIDERS]
    primary = known_sequence[0] if known_sequence else None
    if not task.url and primary in URL_REQUIRED_PROVIDERS and primary != "brightdata-dataset":
        failures.append(
            {
                "type": "provider_missing_url_constraint_violation",
                "message": "Plan primary provider requires a starting URL",
                "provider": primary,
            }
        )
    if primary == "brightdata-serp" and not task.serp_query:
        failures.append(
            {
                "type": "provider_missing_serp_query",
                "message": "Bright Data SERP requires a search query in the goal",
                "provider": primary,
            }
        )
    if task.raw_http and not _is_raw_http_url(task.url):
        failures.append(
            {
                "type": "provider_raw_http_url_constraint_violation",
                "message": _raw_http_url_requirement_message(task.url),
                "url": task.url,
                "allowed_schemes": list(RAW_HTTP_URL_SCHEMES),
            }
        )
    if task.providers_allowed:
        disallowed = [name for name in known_sequence if name not in task.providers_allowed]
        if disallowed:
            failures.append(
                {
                    "type": "provider_allowlist_violation",
                    "message": "Plan provider sequence violates task providers_allowed",
                    "providers": disallowed,
                    "providers_allowed": task.providers_allowed,
                }
            )
    if _is_file_url(task.url):
        disallowed_file_providers = [name for name in known_sequence if name != "playwright"]
        if disallowed_file_providers:
            failures.append(
                {
                    "type": "provider_file_url_constraint_violation",
                    "message": "Local file URLs can only be routed to Playwright",
                    "providers": disallowed_file_providers,
                }
            )
    if task.max_cost_usd is not None:
        over_budget = [
            {
                "provider": name,
                "estimated_floor_usd": estimate_provider_cost(name, task)["estimated_floor_usd"],
                "max_cost_usd": task.max_cost_usd,
            }
            for name in known_sequence
            if estimate_provider_cost(name, task)["estimated_floor_usd"] > task.max_cost_usd
        ]
        if over_budget:
            failures.append(
                {
                    "type": "provider_cost_constraint_violation",
                    "message": "Plan provider sequence violates task max_cost_usd",
                    "providers": over_budget,
                    "max_cost_usd": task.max_cost_usd,
                }
            )
    if task.profile:
        if not ProfileStore(create=False).get(task.profile):
            failures.append(
                {
                    "type": "provider_profile_missing",
                    "message": f"Named profile {task.profile!r} was not found in ProfileStore",
                    "profile": task.profile,
                }
            )
        unsupported = [name for name in known_sequence if not PROVIDERS[name].supports_profiles]
        if unsupported:
            failures.append(
                {
                    "type": "provider_profile_constraint_violation",
                    "message": "Plan provider sequence includes providers that do not support named profiles",
                    "providers": unsupported,
                    "profile": task.profile,
                }
            )
    if task.proxy:
        unsupported_proxy = [
            name for name in known_sequence if name not in {"decodo-http"} and not PROVIDERS[name].supports_proxy_injection
        ]
        if unsupported_proxy:
            failures.append(
                {
                    "type": "provider_proxy_constraint_violation",
                    "message": "Plan provider sequence includes providers that do not support proxy injection",
                    "providers": unsupported_proxy,
                    "proxy": task.proxy,
                }
            )
    return failures


def _task_from_plan(plan: Plan | dict[str, Any]) -> TaskSpec | None:
    if isinstance(plan, Plan):
        try:
            parsed = TaskSpec(**plan.task.to_dict())
            _validate_task_constraints(parsed)
        except ValueError:
            return None
        return parsed
    task = plan.get("task") if isinstance(plan, dict) else None
    if not isinstance(task, dict):
        return None
    try:
        parsed = TaskSpec(**task)
        _validate_task_constraints(parsed)
        return parsed
    except (TypeError, ValueError):
        return None


def _declared_target_scope(plan: Plan | dict[str, Any]) -> Any:
    if isinstance(plan, Plan):
        return plan.task.target_scope
    task = plan.get("task") if isinstance(plan, dict) else None
    if isinstance(task, dict):
        return task.get("target_scope")
    return None


def _plan_provider_sequence(plan: Plan | dict[str, Any]) -> list[str]:
    if isinstance(plan, Plan):
        values = [plan.primary_provider, *plan.fallback_providers]
    else:
        values = [plan.get("primary_provider"), *(plan.get("fallback_providers") or [])]
    sequence = []
    for value in values:
        if isinstance(value, str) and value:
            sequence.append(value)
        elif value is not None:
            sequence.append(str(value))
    return sequence


def _extract_url(goal: str) -> str | None:
    match = re.search(r"(?:https?|file)://\S+", goal)
    return match.group(0).rstrip(TRAILING_EXTRACTED_URL_CHARS) if match else None


def _validate_providers_allowed(providers_allowed: list[str] | None) -> list[str]:
    if providers_allowed is None:
        return []
    if not isinstance(providers_allowed, list):
        raise ValueError("providers_allowed must be a list of provider names")
    non_strings = [item for item in providers_allowed if not isinstance(item, str)]
    if non_strings:
        raise ValueError("providers_allowed entries must be strings")
    names = list(providers_allowed)
    unknown = [name for name in names if name not in PROVIDERS]
    if unknown:
        allowed = ", ".join(PROVIDERS)
        raise ValueError(f"Unknown provider in providers_allowed: {', '.join(unknown)}. Allowed providers: {allowed}")
    return names


def _validate_goal(goal: str) -> str:
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("goal must be a non-empty string")
    return goal.strip()


def _validate_url(url: str | None) -> str | None:
    if url is None:
        return None
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url must be a non-empty string")
    value = url.strip()
    if any(char.isspace() for char in value):
        raise ValueError("url must not contain whitespace; percent-encode spaces as %20")
    parsed = urlparse(value)
    if not parsed.scheme:
        raise ValueError("url must include a scheme, such as https://")
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        allowed = ", ".join(ALLOWED_URL_SCHEMES)
        raise ValueError(f"url must use one of these schemes: {allowed}")
    if parsed.username or parsed.password:
        raise ValueError("url must not contain username or password credentials; use environment variables or authenticated sessions instead")
    if parsed.scheme.lower() == "file":
        if parsed.netloc and parsed.netloc.lower() not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("file URL must be local")
        if not parsed.path:
            raise ValueError("file URL must include a path")
        return value
    if not parsed.netloc:
        raise ValueError("url must include a host")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("url port must be a valid integer") from exc
    return value


def _validate_optimize(optimize: str) -> str:
    if optimize not in OPTIMIZE_VALUES:
        allowed = ", ".join(OPTIMIZE_VALUES)
        raise ValueError(f"Invalid optimize value: {optimize}. Allowed values: {allowed}")
    return optimize


def _validate_max_cost_usd(max_cost_usd: float | None) -> float | None:
    if max_cost_usd is None:
        return None
    if isinstance(max_cost_usd, bool) or not isinstance(max_cost_usd, Real):
        raise ValueError("max_cost_usd must be a number")
    if not math.isfinite(float(max_cost_usd)):
        raise ValueError("max_cost_usd must be finite")
    if max_cost_usd < 0:
        raise ValueError("max_cost_usd must be >= 0")
    return float(max_cost_usd)


def _validate_timeout_seconds(timeout_seconds: int | None) -> int | None:
    if timeout_seconds is None:
        return None
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, Real):
        raise ValueError("timeout_seconds must be a number")
    if not math.isfinite(float(timeout_seconds)):
        raise ValueError("timeout_seconds must be finite")
    if int(timeout_seconds) != float(timeout_seconds):
        raise ValueError("timeout_seconds must be an integer")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be >= 1")
    return int(timeout_seconds)


def _validate_profile_name(profile: str | None) -> str | None:
    if profile is None:
        return None
    if not isinstance(profile, str) or not profile.strip():
        raise ValueError("profile must be a non-empty string when provided")
    normalized = profile.strip()
    if any(char.isspace() for char in normalized):
        raise ValueError("profile must not contain whitespace")
    if len(normalized) > 64:
        raise ValueError("profile must be 64 characters or fewer")
    return normalized


def _validate_proxy_hint(proxy: str | None) -> str | None:
    if proxy is None:
        return None
    if not isinstance(proxy, str) or not proxy.strip():
        raise ValueError("proxy must be a non-empty string when provided")
    return proxy.strip()


def _ensure_profile_exists(name: str) -> None:
    if not ProfileStore(create=False).get(name):
        raise ValueError(f"Profile not found: {name}. Create it with `super-browser profiles create --name {name}`.")


def _validate_task_constraints(task: TaskSpec) -> None:
    task.goal = _validate_goal(task.goal)
    task.url = _validate_url(task.url)
    task.target_scope = _target_scope_for_url(task.url)
    task.providers_allowed = _validate_providers_allowed(task.providers_allowed)
    task.optimize = _validate_optimize(task.optimize)
    task.max_cost_usd = _validate_max_cost_usd(task.max_cost_usd)
    task.timeout_seconds = _validate_timeout_seconds(task.timeout_seconds)
    task.profile = _validate_profile_name(task.profile)
    task.proxy = _validate_proxy_hint(task.proxy)


def _validate_planning_requirements(task: TaskSpec) -> None:
    if task.raw_http and not _is_raw_http_url(task.url):
        raise ValueError(_raw_http_url_requirement_message(task.url))
    if task.serp_query and task.raw_http:
        raise ValueError("SERP tasks cannot also be classified as raw HTTP.")
    if task.serp_query and task.url and not task.structured_extract:
        raise ValueError("SERP tasks should not include a page URL unless structured extraction is also requested.")


def _infer_serp_query(goal: str, url: str | None) -> str | None:
    text = goal.strip()
    lowered = text.lower()
    if url and not _contains_any(lowered, SERP_TERMS):
        return None
    prefixes = (
        "google search:",
        "bing search:",
        "serp:",
        "search results for",
        "search for",
        "google:",
    )
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return text[len(prefix) :].strip()
    if _contains_any(lowered, SERP_TERMS) and not url:
        for marker in SERP_TERMS:
            if marker in lowered:
                parts = lowered.split(marker, 1)
                if len(parts) == 2 and parts[1].strip():
                    return parts[1].strip(" :")
        return text
    return None


def _infer_serp_engine(text: str) -> str:
    if "bing" in text:
        return "bing"
    if "yandex" in text:
        return "yandex"
    return "google"


def _infer_serp_geo(text: str) -> str | None:
    match = re.search(r"\bgeo[:=]\s*([a-z]{2})\b", text)
    if match:
        return match.group(1)
    match = re.search(r"\bcountry[:=]\s*([a-z]{2})\b", text)
    if match:
        return match.group(1)
    return None


def _infer_structured_extract(goal: str, url: str | None) -> bool:
    if _contains_any(goal.lower(), STRUCTURED_EXTRACT_TERMS):
        return True
    if url and dataset_tool_for_url(url) is not None:
        return True
    return False


def _is_file_url(url: str | None) -> bool:
    return bool(url and urlparse(url).scheme.lower() == "file")


def _is_raw_http_url(url: str | None) -> bool:
    return bool(url and urlparse(url).scheme.lower() in RAW_HTTP_URL_SCHEMES)


def _raw_http_url_requirement_message(url: str | None) -> str:
    if url:
        return "Raw HTTP/API tasks require an http(s) starting URL; use browser/local fixture workflows for file or non-HTTP targets."
    return "Raw HTTP/API tasks require an http(s) starting URL; add --url or include an http(s) URL in the goal."


def target_scope_for_url(url: str | None) -> TargetScope:
    return _target_scope_for_url(_validate_url(url))


def _canonical_ipv4(host: str) -> str | None:
    # Browsers accept octal/hex/decimal and short-dotted IPv4 forms
    # (e.g. 0177.0.0.1, 0x7f000001, 2130706433, 127.1) that ipaddress.ip_address
    # rejects. Normalize them via inet_aton so obfuscated loopback/private
    # targets classify correctly instead of falling through to public_web.
    try:
        return socket.inet_ntoa(socket.inet_aton(host))
    except OSError:
        return None


def _target_scope_for_url(url: str | None) -> TargetScope:
    if not url:
        return "none"
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme == "file":
        return "local_file"
    if scheme not in {"http", "https"}:
        return "none"
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return "none"
    if host in LOCAL_HOSTNAMES:
        return "loopback"
    try:
        address = ip_address(host)
    except ValueError:
        canonical = _canonical_ipv4(host)
        if canonical is not None:
            address = ip_address(canonical)
        elif host.endswith(PRIVATE_HOST_SUFFIXES) or "." not in host:
            return "private_network"
        else:
            return "public_web"
    if address.is_loopback:
        return "loopback"
    if address.is_link_local:
        return "link_local"
    if address.is_private or address.is_unspecified or address.is_reserved:
        return "private_network"
    return "public_web"


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(_contains_term(text, term) for term in terms)


def _contains_term(text: str, term: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _missing_env(provider_names: list[str]) -> list[str]:
    from .brightdata.zones import missing_env_for_lane

    missing: list[str] = []
    for name in provider_names:
        if name.startswith("brightdata-"):
            for env_name in missing_env_for_lane(name):
                if env_name not in missing:
                    missing.append(env_name)
            continue
        for env_name in _required_env_for(name):
            if not os.environ.get(env_name) and env_name not in missing:
                missing.append(env_name)
    return missing


def _required_env_for(provider_name: str) -> list[str]:
    return list(PROVIDERS[provider_name].env_vars)


def _needs_council(task: TaskSpec) -> bool:
    ranked = rank_providers(task)
    cloud_tier = {"hyperbrowser", "steel", "browser-use", "airtop", "brightdata-browser", "brightdata-unlocker", "brightdata-dataset"}
    cloud_ambiguous = (
        len(ranked) >= 2
        and ranked[0] in cloud_tier
        and ranked[1] in cloud_tier
    )
    return any(
        [
            task.requires_auth,
            task.anti_bot_risk,
            task.needs_desktop,
            task.external_write,
            task.long_running,
            task.target_scope in {"loopback", "private_network", "link_local", "local_file"},
            cloud_ambiguous,
        ]
    )


def _purpose_for(provider_name: str, task: TaskSpec) -> str:
    if provider_name == "orgo":
        return "Run a full desktop/computer workflow because browser-only automation is insufficient."
    if provider_name == "decodo-http":
        return "Use raw HTTP and residential proxy routing because rendering is not required."
    if provider_name == "browser-use":
        return "Use hardened cloud browser automation for anti-bot or complex browser work."
    if provider_name == "airtop":
        return "Use Airtop cloud sessions and page-query extraction."
    if provider_name == "hyperbrowser":
        return "Use Hyperbrowser cloud scraping for live-gated scale workflows."
    if provider_name == "steel":
        return "Use Steel cloud browser sessions through Playwright CDP."
    if provider_name == "brightdata-unlocker":
        return "Use Bright Data Web Unlocker for one-shot anti-bot page fetch."
    if provider_name == "brightdata-serp":
        return "Use Bright Data SERP API for search engine results."
    if provider_name == "brightdata-dataset":
        return "Use Bright Data dataset extractors for structured platform records."
    if provider_name == "brightdata-browser":
        return "Use Bright Data Scraping Browser for JS-heavy or interactive read flows."
    return "Use local deterministic browser automation."


def _council_report(
    task: TaskSpec,
    ranked: list[str],
    primary: str,
    fallbacks: list[str],
    missing_env: list[str],
    mode: str,
    cost_estimate: dict,
    deliberation: Any | None = None,
) -> dict:
    sequence = [primary, *fallbacks]
    specialists = [_specialist_review(name, task, sequence, primary) for name in ranked if name in PROVIDERS]
    loops = deliberation.loops if deliberation else _review_loops(task, mode, primary, fallbacks)
    deliberation_complete = deliberation.deliberation_complete if deliberation else True
    return {
        "mode": mode,
        "selected_sequence": sequence,
        "specialists_consulted": specialists,
        "review_loops": loops,
        "deliberation_complete": deliberation_complete,
        "deliberation_loop_count": deliberation.loop_count if deliberation else len(loops),
        "execution_pattern": deliberation.execution_pattern if deliberation else "single",
        "combo_steps": deliberation.combo_steps if deliberation else [],
        "documented_recommendations": deliberation.documented_recommendations if deliberation else [],
        "planner_decision": {
            "primary_provider": primary,
            "fallback_providers": fallbacks,
            "missing_env": missing_env,
            "approval_required": approval_required(task),
            "providers_allowed": task.providers_allowed,
            "max_cost_usd": task.max_cost_usd,
            "timeout_seconds": task.timeout_seconds,
            "target_scope": task.target_scope,
            "estimated_cost_floor_usd": cost_estimate["selected_provider_floor_usd"],
            "budget_status": cost_estimate["budget_status"],
        },
        "cost_estimate": cost_estimate,
        "approval_gate": {
            "required": approval_required(task),
            "specialist": "publishing-safety-specialist" if approval_required(task) else None,
            "reason": _approval_gate_reason(task),
        },
        "verifier_contract": {
            "required_artifacts": ["run_report"],
            "checks": ["selected provider matches goal", "fallback attempts are recorded", "external writes are gated"],
        },
    }


def _specialist_review(provider_name: str, task: TaskSpec, sequence: list[str], primary: str) -> dict:
    provider = PROVIDERS[provider_name]
    required_env = _required_env_for(provider_name)
    missing_env = _missing_env([provider_name])
    recommendation = _specialist_recommendation(provider_name, provider_name in sequence, primary, provider.stability)
    return {
        "provider": provider_name,
        "specialist": f"{provider_name}-specialist" if provider_name != "playwright" else "playwright-specialist",
        "recommendation": recommendation,
        "confidence": _specialist_confidence(provider_name, task, recommendation),
        "cost_band": provider.cost_band,
        "estimated_cost_floor_usd": estimate_provider_cost(provider_name, task)["estimated_floor_usd"],
        "stability": provider.stability,
        "required_env": required_env,
        "missing_env": missing_env,
        "best_for": provider.best_for,
        "do_not_use_when": provider.avoid_when,
        "reasons": _specialist_reasons(provider_name, task, recommendation),
        "docs_url": provider.docs_url,
    }


def _specialist_recommendation(provider_name: str, selected: bool, primary: str, stability: str) -> str:
    if provider_name == primary:
        return "use me"
    if selected:
        return "use me only as fallback"
    if stability == "evaluating":
        return "not enough proof"
    return "do not use me"


def _specialist_confidence(provider_name: str, task: TaskSpec, recommendation: str) -> str:
    if recommendation == "not enough proof":
        return "low"
    if PROVIDERS[provider_name].stability == "stable" and recommendation in {"use me", "use me only as fallback"}:
        return "high" if not task.anti_bot_risk or provider_name == "browser-use" else "medium"
    if recommendation == "do not use me":
        return "medium"
    return "medium"


def _specialist_reasons(provider_name: str, task: TaskSpec, recommendation: str) -> list[str]:
    reasons = []
    provider = PROVIDERS[provider_name]
    if recommendation == "use me":
        reasons.append(_purpose_for(provider_name, task))
    elif recommendation == "use me only as fallback":
        reasons.append("Useful if the primary provider is unavailable, blocked, or too expensive for this run.")
    elif recommendation == "not enough proof":
        reasons.append("Provider is live-gated or evaluating; require task-class live tests before production use.")
    else:
        reasons.append("Lower fit than the selected provider sequence for this task.")
    if task.raw_http and provider.supports_raw_http:
        reasons.append("Task is raw HTTP, so browser rendering can be avoided.")
    if task.target_scope in {"loopback", "private_network", "link_local", "local_file"}:
        reasons.append(f"Target scope is {task.target_scope}; do not treat this as ordinary public-web browsing.")
    if task.needs_desktop and provider.supports_desktop:
        reasons.append("Task needs a full desktop/computer surface.")
    if task.anti_bot_risk and provider.supports_anti_bot:
        reasons.append("Task has anti-bot risk and this provider has hardened-browser support.")
    if task.requires_auth and provider.supports_auth:
        reasons.append("Task needs auth/session support.")
    if task.long_running and provider.supports_long_running:
        reasons.append("Task may need long-running or resumable sessions.")
    return reasons


def _review_loops(task: TaskSpec, mode: str, primary: str, fallbacks: list[str]) -> list[dict]:
    loops = [
        {
            "loop": 1,
            "focus": "classification",
            "findings": _rationale(task, primary, mode),
        }
    ]
    if mode == "council":
        loops.append(
            {
                "loop": 2,
                "focus": "provider_sequence",
                "findings": [
                    f"Primary provider: {primary}.",
                    f"Fallback providers: {', '.join(fallbacks) if fallbacks else 'none'}.",
                    "Runtime must record every fallback attempt in run-report.json.",
                    f"Execution timeout: {task.timeout_seconds} second(s)."
                    if task.timeout_seconds
                    else "Execution timeout: provider default.",
                ],
            }
        )
        loops.append(
            {
                "loop": 3,
                "focus": "safety_and_verification",
                "findings": [
                    "Approval is required before credential-bearing or external-write execution."
                    if approval_required(task)
                    else "No approval gate required for this draft-only or read-only plan.",
                    f"Target scope is {task.target_scope}; keep local/internal access explicit."
                    if task.target_scope in {"loopback", "private_network", "link_local", "local_file"}
                    else "Target scope does not require internal-network review.",
                    "Verifier must inspect artifacts, selected provider, attempt order, and confidence.",
                ],
            }
        )
    return loops


def _rationale(task: TaskSpec, primary: str, mode: str) -> list[str]:
    lines = [f"Mode is {mode} because task risk and provider uncertainty were evaluated."]
    lines.append(f"Primary provider is {primary} based on capability scoring.")
    if task.external_write:
        lines.append("External write detected; approval is required before publishing, posting, commenting, replying, messaging, sending, or submitting.")
    if task.draft_only:
        lines.append("Draft-only instruction detected; prepare text without publishing, posting, commenting, replying, messaging, sending, or submitting.")
    if task.anti_bot_risk:
        lines.append("Anti-bot risk detected; hardened/cloud providers are prioritized.")
    if task.requires_auth:
        lines.append("Authenticated session need detected; profile/session-capable providers are prioritized.")
    if task.profile:
        lines.append(f"Named profile {task.profile!r} is bound; only profile-capable providers are eligible.")
    if task.proxy:
        lines.append("Proxy injection requested; providers with upstream proxy support are prioritized.")
    if task.needs_desktop:
        lines.append("Desktop need detected; computer-use backends are prioritized.")
    if task.raw_http:
        lines.append("Raw HTTP need detected; browser rendering is deprioritized.")
    if task.target_scope in {"loopback", "private_network", "link_local", "local_file"}:
        lines.append(f"Target scope is {task.target_scope}; local/internal routing boundaries are explicit.")
    if task.providers_allowed:
        lines.append(f"Provider allowlist enforced: {', '.join(task.providers_allowed)}.")
    if task.max_cost_usd is not None:
        lines.append(f"Cost ceiling enforced at max_cost_usd={task.max_cost_usd}.")
    if task.timeout_seconds is not None:
        lines.append(f"Execution timeout enforced at timeout_seconds={task.timeout_seconds}.")
    return lines


def _approval_gate_reason(task: TaskSpec) -> str:
    if approval_required(task):
        if task.target_scope == "private_network":
            return "private-network target requires explicit approval"
        if task.target_scope == "link_local":
            return "link-local target requires explicit approval"
        if task.target_scope == "local_file":
            return "local file target requires explicit approval"
        return "external write, destructive, or credential-bearing workflow"
    if task.draft_only:
        return "draft-only workflow; publishing/posting/commenting/replying/messaging/sending/submitting remains disallowed"
    return "read-only workflow"
