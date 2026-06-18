from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .env_file import find_repo_root, load_env_file
from .bundle import build_bundle_manifest, write_bundle_manifest
from .env_checklist import environment_checklist
from .fleet import create_fleet_runs
from .models import RUN_STATUS_VALUES
from .profiles import ProfileStore
from .production import production_readiness
from .providers import PROVIDERS, list_providers, provider_readiness
from .redaction import redact_text, safe_json_dumps
from .router import build_plan, infer_task
from .runtime import approve_run, create_run, deny_run, resume_run
from .setup_helpers import install_skill_bundle, mcp_config, write_mcp_config
from .setup_walkthrough import launch_setup
from .store import RunStore
from .handoff import build_handoff
from .live_tests import WORKFLOW_CLASSES, run_live_tests
from .verifier import verify_run
from . import agent as slack_agent


def main(argv: list[str] | None = None) -> int:
    root = find_repo_root()
    if root and not os.environ.get("SUPER_BROWSER_SKIP_ENV_FILE"):
        load_env_file(root / ".env")
    parser = argparse.ArgumentParser(prog="super-browser", description="Plan and route browser/computer automation tasks.")
    sub = parser.add_subparsers(dest="command", required=True)

    plan_p = sub.add_parser("plan", help="Plan a browser automation task.")
    plan_p.add_argument("--goal", required=True)
    plan_p.add_argument("--url")
    plan_p.add_argument("--optimize", choices=["balanced", "cost", "reliability"], default="balanced")
    plan_p.add_argument("--allow-provider", action="append", choices=list(PROVIDERS.keys()), default=[])
    plan_p.add_argument("--max-cost-usd", type=float)
    plan_p.add_argument("--timeout-seconds", type=_positive_int)
    plan_p.add_argument("--profile", help="Named persistent browser profile from ProfileStore.")
    plan_p.add_argument("--proxy", help="Proxy hint (decodo/auto/sticky or full proxy URL).")
    plan_p.add_argument(
        "--deliberation-rounds",
        type=_deliberation_rounds,
        help="Planner deliberation loops (3-5). Default: 3 direct, 5 council.",
    )

    run_p = sub.add_parser("run", help="Create and execute a durable browser automation run when policy allows.")
    run_p.add_argument("--goal", required=True)
    run_p.add_argument("--url")
    run_p.add_argument("--optimize", choices=["balanced", "cost", "reliability"], default="balanced")
    run_p.add_argument("--allow-provider", action="append", choices=list(PROVIDERS.keys()), default=[])
    run_p.add_argument("--max-cost-usd", type=float)
    run_p.add_argument("--timeout-seconds", type=_positive_int)
    run_p.add_argument("--profile", help="Named persistent browser profile from ProfileStore.")
    run_p.add_argument("--proxy", help="Proxy hint (decodo/auto/sticky or full proxy URL).")
    run_p.add_argument("--fleet", type=_fleet_size, help="Create 2-10 coordinated fleet runs with per-member profiles.")
    run_p.add_argument("--plan-only", action="store_true", help="Create the durable run plan without executing the provider.")
    run_p.add_argument(
        "--deliberation-rounds",
        type=_deliberation_rounds,
        help="Planner deliberation loops (3-5). Default: 3 direct, 5 council.",
    )

    profiles_p = sub.add_parser("profiles", help="Manage named persistent browser profiles.")
    profiles_sub = profiles_p.add_subparsers(dest="profiles_command", required=True)
    profiles_create = profiles_sub.add_parser("create", help="Create a named browser profile.")
    profiles_create.add_argument("--name", required=True)
    profiles_create.add_argument("--description", default="")
    profiles_create.add_argument("--preferred-provider", choices=list(PROVIDERS.keys()))
    profiles_sub.add_parser("list", help="List saved browser profiles.")
    profiles_get = profiles_sub.add_parser("get", help="Return one browser profile by name.")
    profiles_get.add_argument("name")
    profiles_delete = profiles_sub.add_parser("delete", help="Delete a browser profile.")
    profiles_delete.add_argument("name")

    resume_p = sub.add_parser("resume", help="Resume a planned, approved, blocked, or failed run when policy allows.")
    resume_p.add_argument("run_id")

    get_p = sub.add_parser("get", help="Return a saved run by id without executing it.")
    get_p.add_argument("run_id")

    handoff_p = sub.add_parser("handoff", help="Return a compact handoff package for another agent.")
    handoff_p.add_argument("run_id")

    runs_p = sub.add_parser("runs", aliases=["list-runs"], help="List saved runs without executing them.")
    runs_p.add_argument("--status", choices=RUN_STATUS_VALUES)
    runs_p.add_argument("--limit", type=_positive_int, default=20)
    runs_p.add_argument("--details", action="store_true", help="Include full run payloads instead of compact summaries.")

    verify_p = sub.add_parser("verify", help="Verify a run report.")
    verify_p.add_argument("run_id")

    approve_p = sub.add_parser("approve", help="Approve a run that is awaiting approval.")
    approve_p.add_argument("run_id")
    approve_p.add_argument("--by", default="user")
    approve_p.add_argument("--reason", required=True)
    approve_p.add_argument("--execute", action="store_true", help="Execute the provider immediately after recording approval.")

    deny_p = sub.add_parser("deny", help="Deny a run that is awaiting approval.")
    deny_p.add_argument("run_id")
    deny_p.add_argument("--by", default="user")
    deny_p.add_argument("--reason", required=True)

    sub.add_parser("providers", help="List known browser/computer providers.")
    sub.add_parser("doctor", help="Check provider environment readiness.")
    prod_p = sub.add_parser("production-readiness", help="Fail unless required providers have production-ready live evidence.")
    prod_p.add_argument("--require-provider", action="append", choices=list(PROVIDERS.keys()), default=[])
    manifest_p = sub.add_parser("bundle-manifest", help="Print or write a hashed Super Saiyan Browser handoff manifest.")
    manifest_p.add_argument("--root", help="Repository or installed bundle root to inspect.")
    manifest_p.add_argument("--path", help="Write manifest JSON to this path instead of only printing it.")
    sub.add_parser("env-checklist", help="Print required and optional Super Saiyan Browser environment variables without values.")
    setup_p = sub.add_parser(
        "setup",
        help="Return a step-by-step install walkthrough for agents (clone, pip, skills, MCP, doctor).",
    )
    setup_p.add_argument(
        "--client",
        choices=["cursor", "codex", "claude"],
        help="Optional agent client hint to tailor install-skill and init-mcp commands.",
    )
    live_p = sub.add_parser("live-test", help="Run gated local/provider live tests.")
    live_p.add_argument("--provider", choices=["local", "fixtures", "all", *PROVIDERS.keys()], default="local")
    live_p.add_argument("--workflow-class", choices=list(WORKFLOW_CLASSES), default="default")

    serp_p = sub.add_parser("serp", help="Run a Bright Data SERP query.")
    serp_p.add_argument("--query", required=True)
    serp_p.add_argument("--engine", choices=["google", "bing", "yandex"], default="google")
    serp_p.add_argument("--geo", help="Two-letter country code for geo-targeted SERP.")
    serp_p.add_argument("--timeout-seconds", type=_positive_int)

    unlock_p = sub.add_parser("unlock", help="Fetch a protected URL via Bright Data Web Unlocker.")
    unlock_p.add_argument("--url", required=True)
    unlock_p.add_argument("--format", choices=["markdown", "html"], default="markdown")
    unlock_p.add_argument("--timeout-seconds", type=_positive_int)

    dataset_p = sub.add_parser("dataset", help="Fetch structured platform data via Bright Data datasets.")
    dataset_p.add_argument("--url", help="Platform URL to scrape with an auto-selected dataset tool.")
    dataset_p.add_argument("--tool", help="Explicit dataset tool name (e.g. linkedin_company_profile).")
    dataset_p.add_argument("--dataset-id", help="Dataset id for filter search.")
    dataset_p.add_argument("--filter-json", help="JSON filter tree for search_dataset.")
    dataset_p.add_argument("--size", type=_positive_int, default=10)
    dataset_p.add_argument("--timeout-seconds", type=_positive_int)

    hunt_p = sub.add_parser("hunt", help="Run the lead-scraper hunt orchestrator.")
    hunt_p.add_argument("--niche", required=True)
    hunt_p.add_argument("--sources", nargs="*", default=[])
    hunt_p.add_argument("--dry-run", action="store_true")

    bd_p = sub.add_parser(
        "brightdata-discover",
        help="Auto-discover Bright Data zones from your account (API key or Cursor MCP).",
    )
    bd_p.add_argument("--write-env", action="store_true", help="Merge discovered values into the repo .env file.")
    bd_p.add_argument("--force", action="store_true", help="Overwrite existing Bright Data entries in .env.")

    install_p = sub.add_parser("install-skill", help="Install a self-contained Super Saiyan Browser skill/plugin bundle.")
    install_p.add_argument("--target", help="Directory that should receive the super-browser bundle.")
    install_p.add_argument("--name", default="super-browser", help="Installed bundle directory name.")
    install_p.add_argument("--force", action="store_true", help="Update an existing bundle in place.")

    init_mcp_p = sub.add_parser("init-mcp", help="Print or write MCP server config.")
    init_mcp_p.add_argument("--path", help="Write MCP config JSON to this file instead of only printing it.")
    init_mcp_p.add_argument("--cwd", help="Repository or installed bundle path for the MCP server.")
    init_mcp_p.add_argument("--force", action="store_true", help="Overwrite an existing MCP config file.")
    init_mcp_p.add_argument("--merge", action="store_true", help="Merge super-browser into an existing MCP config without removing other servers.")

    agent_p = sub.add_parser("agent", help="Start the optional Slack Socket Mode daemon (Level 2 ingress).")
    agent_p.add_argument(
        "--execute-on-approve",
        action="store_true",
        help="Execute provider runs immediately after Slack approval (default: env SUPER_BROWSER_SLACK_EXECUTE).",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "providers":
            return _print(list_providers())
        if args.command == "doctor":
            return _print({"providers": provider_readiness()})
        if args.command == "production-readiness":
            payload = production_readiness(required_providers=args.require_provider or None)
            _print(payload)
            return 0 if payload["production_ready"] else 1
        if args.command == "bundle-manifest":
            if args.path:
                return _print(write_bundle_manifest(root=args.root, path=args.path))
            return _print(build_bundle_manifest(root=args.root))
        if args.command == "env-checklist":
            return _print(environment_checklist())
        if args.command == "setup":
            return _print(launch_setup(client=args.client))
        if args.command == "live-test":
            return _print(run_live_tests(args.provider, workflow_class=args.workflow_class))
        if args.command == "serp":
            goal = f"Google search: {args.query}"
            run = create_run(
                goal,
                optimize="reliability",
                execute=True,
                providers_allowed=["brightdata-serp"],
                timeout_seconds=args.timeout_seconds,
                deliberation_rounds=3,
            )
            return _print(run.to_dict())
        if args.command == "unlock":
            goal = f"Unlock protected page content from {args.url}"
            run = create_run(
                goal,
                url=args.url,
                optimize="cost",
                execute=True,
                providers_allowed=["brightdata-unlocker"],
                timeout_seconds=args.timeout_seconds,
                deliberation_rounds=3,
            )
            return _print(run.to_dict())
        if args.command == "dataset":
            if args.filter_json:
                import json as json_module

                from .brightdata.datasets import search_dataset

                filter_payload = json_module.loads(args.filter_json)
                dataset_id = args.dataset_id or filter_payload.get("dataset_id")
                if not dataset_id:
                    return _error("dataset filter search requires --dataset-id or dataset_id in --filter-json")
                filter_tree = filter_payload.get("filter", filter_payload)
                return _print(
                    search_dataset(
                        dataset_id,
                        filter_tree,
                        size=args.size,
                        timeout_seconds=args.timeout_seconds or 180,
                    )
                )
            if not args.url:
                return _error("dataset requires --url or --filter-json")
            goal = f"Extract structured company profile from {args.url}" if not args.tool else f"Extract structured {args.tool} from {args.url}"
            run = create_run(
                goal,
                url=args.url,
                optimize="reliability",
                execute=True,
                providers_allowed=["brightdata-dataset"],
                timeout_seconds=args.timeout_seconds,
                deliberation_rounds=3,
            )
            if args.tool:
                run.plan["task"]["dataset_tool"] = args.tool
                RunStore().save(run)
                if run.status not in {"complete", "failed", "blocked"}:
                    run = resume_run(run.run_id)
            return _print(run.to_dict())
        if args.command == "hunt":
            from .lead_scraper.hunt import run_hunt

            return _print(run_hunt(args.niche, sources=args.sources, dry_run=args.dry_run))
        if args.command == "brightdata-discover":
            from .brightdata.zone_discovery import discover_and_apply, discovery_report, write_discovered_env

            if args.write_env:
                env_path = (root or find_repo_root() or Path.cwd()) / ".env"
                payload = write_discovered_env(env_path, force=args.force)
            else:
                discover_and_apply()
                payload = discovery_report()
            return _print(payload)
        if args.command == "plan":
            task = infer_task(
                args.goal,
                url=args.url,
                optimize=args.optimize,
                providers_allowed=args.allow_provider,
                max_cost_usd=args.max_cost_usd,
                timeout_seconds=args.timeout_seconds,
                profile=args.profile,
                proxy=args.proxy,
            )
            return _print(build_plan(task, deliberation_rounds=args.deliberation_rounds).to_dict())
        if args.command == "profiles":
            store = ProfileStore()
            if args.profiles_command == "create":
                profile = store.create(
                    args.name,
                    description=args.description,
                    preferred_provider=args.preferred_provider,
                )
                return _print(profile.to_dict())
            if args.profiles_command == "list":
                return _print([item.to_dict() for item in store.list()])
            if args.profiles_command == "get":
                profile = store.get(args.name)
                if not profile:
                    return _error(f"Profile not found: {args.name}")
                return _print(profile.to_dict())
            if args.profiles_command == "delete":
                deleted = store.delete(args.name)
                if not deleted:
                    return _error(f"Profile not found: {args.name}")
                return _print({"deleted": args.name})
        if args.command == "run":
            if args.fleet:
                return _print(
                    create_fleet_runs(
                        args.goal,
                        fleet_size=args.fleet,
                        url=args.url,
                        optimize=args.optimize,
                        execute=not args.plan_only,
                        providers_allowed=args.allow_provider,
                        max_cost_usd=args.max_cost_usd,
                        timeout_seconds=args.timeout_seconds,
                        profile=args.profile,
                        proxy=args.proxy,
                    )
                )
            run = create_run(
                args.goal,
                url=args.url,
                optimize=args.optimize,
                execute=not args.plan_only,
                providers_allowed=args.allow_provider,
                max_cost_usd=args.max_cost_usd,
                timeout_seconds=args.timeout_seconds,
                profile=args.profile,
                proxy=args.proxy,
                deliberation_rounds=args.deliberation_rounds,
            )
            return _print(run.to_dict())
        if args.command == "resume":
            return _print(resume_run(args.run_id).to_dict())
        if args.command == "get":
            run = RunStore(create=False).get(args.run_id)
            if not run:
                return _error(f"Run not found: {args.run_id}")
            return _print(run)
        if args.command == "handoff":
            return _print(build_handoff(args.run_id))
        if args.command in ("runs", "list-runs"):
            return _print(RunStore(create=False).list(status=args.status, limit=args.limit, include_details=args.details))
        if args.command == "verify":
            return _print(verify_run(args.run_id))
        if args.command == "approve":
            return _print(approve_run(args.run_id, approver=args.by, reason=args.reason, execute=args.execute).to_dict())
        if args.command == "deny":
            return _print(deny_run(args.run_id, denied_by=args.by, reason=args.reason).to_dict())
        if args.command == "install-skill":
            return _print(install_skill_bundle(args.target, name=args.name, force=args.force))
        if args.command == "init-mcp":
            if args.path:
                return _print(write_mcp_config(args.path, force=args.force, merge=args.merge, cwd=args.cwd))
            return _print(mcp_config(cwd=args.cwd))
        if args.command == "agent":
            slack_agent.run_slack_daemon(execute_on_approve=True if args.execute_on_approve else None)
            return 0
        return _error("Unknown command")
    except Exception as exc:
        return _error_from_exception(exc)


def _print(payload: object) -> int:
    print(safe_json_dumps(payload))
    return 0


def _error(message: str, *, error_type: str = "ValueError") -> int:
    print(json.dumps({"error": redact_text(message), "error_type": error_type}), file=sys.stderr)
    return 1


def _error_from_exception(exc: Exception) -> int:
    return _error(str(exc), error_type=exc.__class__.__name__)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def _fleet_size(value: str) -> int:
    parsed = int(value)
    if parsed < 2 or parsed > 10:
        raise argparse.ArgumentTypeError("fleet size must be between 2 and 10")
    return parsed


def _deliberation_rounds(value: str) -> int:
    parsed = int(value)
    if parsed < 3 or parsed > 5:
        raise argparse.ArgumentTypeError("deliberation rounds must be between 3 and 5")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
