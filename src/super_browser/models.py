from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from .redaction import redact, redact_text, safe_json_dumps


RiskLevel = Literal["read", "mutating", "external_write", "credential", "destructive"]
TargetScope = Literal["none", "public_web", "loopback", "private_network", "link_local", "local_file"]
RUN_STATUS_VALUES = ["planned", "awaiting_approval", "approved", "denied", "executing", "blocked", "verifying", "complete", "failed"]
RunStatus = Literal["planned", "awaiting_approval", "approved", "denied", "executing", "blocked", "verifying", "complete", "failed"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskSpec:
    goal: str
    url: str | None = None
    task_type: str = "auto"
    requires_auth: bool = False
    anti_bot_risk: bool = False
    needs_desktop: bool = False
    external_write: bool = False
    draft_only: bool = False
    raw_http: bool = False
    serp_query: str | None = None
    serp_engine: str = "google"
    serp_geo: str | None = None
    structured_extract: bool = False
    dataset_tool: str | None = None
    dataset_filter: dict[str, Any] | None = None
    long_running: bool = False
    target_scope: TargetScope = "none"
    optimize: Literal["reliability", "cost", "balanced"] = "balanced"
    max_cost_usd: float | None = None
    timeout_seconds: int | None = None
    providers_allowed: list[str] = field(default_factory=list)
    profile: str | None = None
    proxy: str | None = None
    fleet_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderCapability:
    name: str
    display_name: str
    stability: Literal["stable", "evaluating", "docs-only"]  # docs-only: SSOT + deliberation, no adapter dispatch
    cost_band: Literal["free", "low", "medium", "high", "variable"]
    env_vars: list[str]
    docs_url: str
    best_for: list[str]
    avoid_when: list[str]
    supports_auth: bool = False
    supports_desktop: bool = False
    supports_raw_http: bool = False
    supports_serp: bool = False
    supports_structured_extract: bool = False
    supports_unlocked_http: bool = False
    supports_anti_bot: bool = False
    supports_long_running: bool = False
    supports_captcha: bool = False
    supports_profiles: bool = False
    supports_proxy_injection: bool = False
    supports_fleet: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanStep:
    order: int
    provider: str
    purpose: str
    risk: RiskLevel = "read"
    required_env: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Plan:
    task: TaskSpec
    mode: Literal["direct", "council"] = "direct"
    primary_provider: str = "playwright"
    fallback_providers: list[str] = field(default_factory=list)
    steps: list[PlanStep] = field(default_factory=list)
    missing_env: list[str] = field(default_factory=list)
    approval_required: bool = False
    rationale: list[str] = field(default_factory=list)
    council_report: dict[str, Any] = field(default_factory=dict)
    cost_estimate: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["task"] = self.task.to_dict()
        data["steps"] = [step.to_dict() for step in self.steps]
        return data


@dataclass
class RunState:
    run_id: str
    status: RunStatus
    plan: dict[str, Any]
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    execution_lease: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, plan: Plan, status: RunStatus) -> "RunState":
        return cls(
            run_id=f"run_{uuid4().hex[:12]}",
            status=status,
            plan=plan.to_dict(),
            events=[{"at": utc_now(), "type": "run_created", "status": status}],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionResult:
    provider: str
    status: Literal["complete", "blocked", "failed"]
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        self.artifacts = redact(self.artifacts)
        self.events = redact(self.events)
        self.verification = redact(self.verification)
        self.error = redact_text(self.error)

    def to_dict(self) -> dict[str, Any]:
        return redact(asdict(self))


def approval_request_from_plan(plan: Plan, required_before: str = "provider_execution", reason: str | None = None) -> dict[str, Any]:
    task = plan.task
    payload = {
        "at": utc_now(),
        "approval_id": f"approval_{uuid4().hex[:12]}",
        "type": "approval_request",
        "status": "pending",
        "provider": plan.primary_provider,
        "target_url": task.url,
        "goal": task.goal,
        "risk": _approval_risk(task),
        "action_summary": _action_summary(task.goal),
        "action_fingerprint": action_fingerprint_from_plan(plan),
        "plan_sha256": plan_fingerprint(plan),
        "required_before": required_before,
        "missing_env": plan.missing_env,
    }
    if reason:
        payload["reason"] = reason
    return payload


def action_fingerprint_from_plan(plan: Plan | dict[str, Any]) -> str:
    task = plan.task if isinstance(plan, Plan) else plan.get("task", {})
    payload = {
        "action_summary": _action_summary(_task_value(task, "goal", "") or ""),
        "target_url": _task_value(task, "url"),
        "risk": _approval_risk(task),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def plan_fingerprint(plan: Plan | dict[str, Any]) -> str:
    payload = plan.to_dict() if isinstance(plan, Plan) else plan
    return hashlib.sha256(safe_json_dumps(payload).encode("utf-8")).hexdigest()


def _action_summary(goal: str) -> str:
    return str(goal).strip()


def _task_value(task: TaskSpec | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(task, dict):
        return task.get(key, default)
    return getattr(task, key, default)


def _approval_risk(task: TaskSpec | dict[str, Any]) -> str:
    goal = str(_task_value(task, "goal", "") or "")
    policy_risk = None
    try:
        from .policy import infer_risk

        policy_risk = infer_risk(goal)
    except Exception:
        policy_risk = None
    if _task_value(task, "external_write") or policy_risk in {"external_write", "destructive"}:
        return "external_write"
    target_scope = _task_value(task, "target_scope")
    if target_scope == "private_network":
        return "private_network"
    if target_scope == "link_local":
        return "link_local"
    if target_scope == "local_file":
        return "local_file"
    if _task_value(task, "requires_auth") or policy_risk == "credential":
        return "credential"
    return "read"
