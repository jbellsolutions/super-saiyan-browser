from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from .runtime import approve_run, create_run, deny_run, resume_run
from .store import RunStore

log = logging.getLogger("super_browser.agent")

APPROVE_RE = re.compile(r"^\s*approve\s+(?P<run_id>run_[a-f0-9]+)(?:\s+(?P<reason>.+))?$", re.I)
DENY_RE = re.compile(r"^\s*deny\s+(?P<run_id>run_[a-f0-9]+)(?:\s+(?P<reason>.+))?$", re.I)
STATUS_RE = re.compile(r"^\s*status\s+(?P<run_id>run_[a-f0-9]+)\s*$", re.I)
RESUME_RE = re.compile(r"^\s*resume\s+(?P<run_id>run_[a-f0-9]+)\s*$", re.I)


def handle_message(text: str, *, user_id: str = "slack-user", execute: bool = False) -> str:
    """Parse a Slack message and dispatch to the Super Saiyan Browser runtime."""
    stripped = (text or "").strip()
    if not stripped:
        return "Send a browser goal, or `status <run_id>`, `approve <run_id> <reason>`, `deny <run_id> <reason>`, `resume <run_id>`."

    for pattern, handler in (
        (APPROVE_RE, _handle_approve),
        (DENY_RE, _handle_deny),
        (STATUS_RE, _handle_status),
        (RESUME_RE, _handle_resume),
    ):
        match = pattern.match(stripped)
        if match:
            return handler(match.groupdict(), user_id=user_id, execute=execute)

    return _handle_plan_or_run(stripped, user_id=user_id, execute=execute)


def _handle_plan_or_run(goal: str, *, user_id: str, execute: bool) -> str:
    run = create_run(goal, execute=execute)
    payload = run.to_dict()
    plan = payload.get("plan", {})
    council = plan.get("council_report") or {}
    lines = [
        f"run_id: {payload.get('run_id')}",
        f"status: {payload.get('status')}",
        f"provider: {plan.get('primary_provider')}",
        f"deliberation_loops: {council.get('deliberation_loop_count', len(council.get('review_loops', [])))}",
        f"deliberation_complete: {council.get('deliberation_complete', True)}",
    ]
    if plan.get("approval_required"):
        lines.append("approval_required: true — reply `approve <run_id> <reason>` to continue.")
    if plan.get("task", {}).get("profile"):
        lines.append(f"profile: {plan['task']['profile']}")
    if plan.get("task", {}).get("proxy"):
        lines.append(f"proxy: {plan['task']['proxy']}")
    lines.append(f"requested_by: {user_id}")
    return "\n".join(lines)


def _handle_approve(groups: dict[str, str], *, user_id: str, execute: bool) -> str:
    run_id = groups["run_id"]
    reason = (groups.get("reason") or "approved via slack").strip()
    run = approve_run(run_id, approver=user_id, reason=reason, execute=execute)
    return _format_run_summary(run.to_dict())


def _handle_deny(groups: dict[str, str], *, user_id: str, execute: bool) -> str:
    run_id = groups["run_id"]
    reason = (groups.get("reason") or "denied via slack").strip()
    run = deny_run(run_id, denied_by=user_id, reason=reason)
    return _format_run_summary(run.to_dict())


def _handle_status(groups: dict[str, str], *, user_id: str, execute: bool) -> str:
    run_id = groups["run_id"]
    payload = RunStore().get(run_id)
    if not payload:
        return f"Run not found: {run_id}"
    return _format_run_summary(payload)


def _handle_resume(groups: dict[str, str], *, user_id: str, execute: bool) -> str:
    run_id = groups["run_id"]
    run = resume_run(run_id)
    return _format_run_summary(run.to_dict())


def _format_run_summary(payload: dict[str, Any]) -> str:
    plan = payload.get("plan", {})
    verification = payload.get("verification", {})
    return json.dumps(
        {
            "run_id": payload.get("run_id"),
            "status": payload.get("status"),
            "primary_provider": plan.get("primary_provider"),
            "approval_required": plan.get("approval_required"),
            "verification_status": verification.get("status"),
        },
        indent=2,
        sort_keys=True,
    )


def run_slack_daemon(*, execute_on_approve: bool | None = None) -> None:
    """Start Slack Socket Mode ingress for Super Saiyan Browser (optional Level 2 host)."""
    try:
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError as exc:
        raise SystemExit(
            "slack_bolt is required for the Super Saiyan Browser Slack daemon. "
            "Install with `pip install slack-bolt`."
        ) from exc

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    app_token = os.environ.get("SLACK_APP_TOKEN", "").strip()
    if not bot_token or not app_token:
        raise SystemExit("Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN before starting the daemon.")

    if execute_on_approve is None:
        execute_on_approve = os.environ.get("SUPER_BROWSER_SLACK_EXECUTE", "").lower() in {"1", "true", "yes"}

    app = App(token=bot_token)

    @app.event("app_mention")
    def on_mention(event, say):  # type: ignore[no-untyped-def]
        text = _strip_bot_mention(event.get("text", ""))
        user_id = event.get("user", "slack-user")
        say(handle_message(text, user_id=user_id, execute=False))

    @app.message(re.compile(r".+"))
    def on_dm(message, say):  # type: ignore[no-untyped-def]
        if message.get("channel_type") != "im":
            return
        user_id = message.get("user", "slack-user")
        say(handle_message(message.get("text", ""), user_id=user_id, execute=False))

    log.info("Super Saiyan Browser Slack daemon starting (execute_on_approve=%s)", execute_on_approve)
    SocketModeHandler(app, app_token).start()


def _strip_bot_mention(text: str) -> str:
    return re.sub(r"<@[^>]+>\s*", "", text or "").strip()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_slack_daemon()


if __name__ == "__main__":
    main()
