from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

from .bundle import build_bundle_manifest
from .env_checklist import environment_checklist
from .fleet import create_fleet_runs
from .models import RUN_STATUS_VALUES
from .production import production_readiness
from .profiles import ProfileStore
from .providers import PROVIDERS, list_providers, provider_readiness
from .redaction import redact, redact_text, safe_json_dumps
from .router import build_plan, infer_task
from .runtime import approve_run, create_run, deny_run, resume_run
from .store import RunStore
from .handoff import build_handoff
from .live_tests import WORKFLOW_CLASSES, run_live_tests
from .setup_helpers import discover_repo_root, install_skill_bundle, is_super_browser_root, mcp_config, write_mcp_config
from .setup_walkthrough import launch_setup
from .verifier import verify_run


PROVIDER_NAMES = list(PROVIDERS.keys())
OPTIMIZE_VALUES = ["balanced", "cost", "reliability"]
SUPPORTED_PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SERVER_INSTRUCTIONS = (
    "Use plan_browser_task before run_browser_task for nontrivial work. "
    "Use get_browser_run and list_browser_runs for read-only run lookup. "
    "Use resources/list and resources/read for provider docs and routing playbooks. "
    "Use bundle_manifest before handoff or release audits. "
    "Use setup_walkthrough or env_checklist for first-time install without exposing secret values. "
    "External writes and credential-bearing work must stop for approval."
)

RESOURCE_FILES = {
    "super-browser://README": {
        "path": "README.md",
        "name": "README",
        "description": "Super Saiyan Browser architecture, install paths, workflows, provider matrix, and verification status.",
    },
    "super-browser://SKILL": {
        "path": "SKILL.md",
        "name": "Root Skill",
        "description": "Top-level Super Saiyan Browser skill instructions for agents.",
    },
    "super-browser://references/provider-matrix": {
        "path": "references/provider-matrix.md",
        "name": "Provider Matrix",
        "description": "Provider setup, best uses, bad uses, limits, status, and env vars.",
    },
    "super-browser://references/routing-playbook": {
        "path": "references/routing-playbook.md",
        "name": "Routing Playbook",
        "description": "Provider routing, council mode, resume semantics, and verification playbook.",
    },
    "super-browser://references/cost-model": {
        "path": "references/cost-model.md",
        "name": "Cost Model",
        "description": "Cost bands, routing floor estimates, budget ceilings, and cost caveats.",
    },
    "super-browser://references/security-and-approval-policy": {
        "path": "references/security-and-approval-policy.md",
        "name": "Security And Approval Policy",
        "description": "Approval gates, external-write safety, redaction policy, and MCP/CLI safety rules.",
    },
    "super-browser://references/live-test-matrix": {
        "path": "references/live-test-matrix.md",
        "name": "Live Test Matrix",
        "description": "Fixture and provider live-test scenarios and gating rules.",
    },
    "super-browser://references/combo-playbook": {
        "path": "references/combo-playbook.md",
        "name": "Combo Playbook",
        "description": "When to combine providers vs use one tool alone.",
    },
    "super-browser://references/providers/README": {
        "path": "references/providers/README.md",
        "name": "Provider SSOT Index",
        "description": "Per-provider capability documents for deliberation and weekly intelligence.",
    },
    "super-browser://references/providers/browserbase-capability-audit": {
        "path": "references/providers/browserbase-capability-audit.md",
        "name": "Browserbase Capability Audit",
        "description": "Adapter ship/no-ship verdict and trigger criteria for Browserbase.",
    },
    "super-browser://docs/setup-walkthrough": {
        "path": "docs/setup-walkthrough.md",
        "name": "Setup Walkthrough",
        "description": "Step-by-step onboarding for new users: clone, install, API keys, skills, MCP, doctor.",
    },
    "super-browser://docs/agent-quickstart": {
        "path": "docs/agent-quickstart.md",
        "name": "Agent Quickstart",
        "description": "Drop-in quickstart when an agent receives the GitHub repo link.",
    },
}


def _empty_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": False}


def _run_id_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"run_id": {"type": "string", "minLength": 1, "description": "Durable Super Saiyan Browser run id."}},
        "required": ["run_id"],
        "additionalProperties": False,
    }


PLAN_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "goal": {"type": "string", "minLength": 1, "description": "Browser/computer automation goal to plan."},
        "url": {"type": "string", "description": "Optional starting URL."},
        "optimize": {"type": "string", "enum": OPTIMIZE_VALUES, "default": "balanced"},
        "providers_allowed": {
            "type": "array",
            "items": {"type": "string", "enum": PROVIDER_NAMES},
            "description": "Strict provider allowlist.",
        },
        "max_cost_usd": {"type": "number", "minimum": 0, "description": "Optional routing cost ceiling."},
        "timeout_seconds": {"type": "integer", "minimum": 1, "description": "Optional provider execution timeout in seconds."},
        "profile": {"type": "string", "minLength": 1, "description": "Named persistent browser profile from ProfileStore."},
        "proxy": {"type": "string", "minLength": 1, "description": "Proxy hint (decodo/auto/sticky or full proxy URL)."},
        "deliberation_rounds": {
            "type": "integer",
            "minimum": 3,
            "maximum": 5,
            "description": "Planner deliberation loops (3 direct, 5 council).",
        },
    },
    "required": ["goal"],
    "additionalProperties": False,
}

RUN_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        **PLAN_INPUT_SCHEMA["properties"],
        "execute": {"type": "boolean", "default": True, "description": "Whether to execute immediately when policy allows."},
        "fleet_size": {"type": "integer", "minimum": 2, "maximum": 10, "description": "Create 2-10 coordinated fleet runs with per-member profiles."},
    },
    "required": ["goal"],
    "additionalProperties": False,
}

PROFILE_NAME_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string", "minLength": 1, "description": "Profile name."}},
    "required": ["name"],
    "additionalProperties": False,
}

CREATE_PROFILE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string", "default": ""},
        "preferred_provider": {"type": "string", "enum": PROVIDER_NAMES},
    },
    "required": ["name"],
    "additionalProperties": False,
}

APPROVE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string", "minLength": 1},
        "by": {"type": "string", "minLength": 1, "default": "user"},
        "reason": {"type": "string", "minLength": 1, "description": "Required audit note describing the exact approved action."},
        "execute": {"type": "boolean", "default": False},
    },
    "required": ["run_id", "reason"],
    "additionalProperties": False,
}

DENY_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string", "minLength": 1},
        "by": {"type": "string", "minLength": 1, "default": "user"},
        "reason": {"type": "string", "minLength": 1, "description": "Required audit note explaining why the action is denied."},
    },
    "required": ["run_id", "reason"],
    "additionalProperties": False,
}

LIVE_TEST_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "provider": {"type": "string", "enum": ["local", "fixtures", "all", *PROVIDER_NAMES], "default": "local"},
        "workflow_class": {"type": "string", "enum": list(WORKFLOW_CLASSES), "default": "default"},
    },
    "additionalProperties": False,
}

PRODUCTION_READINESS_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "required_providers": {
            "type": "array",
            "items": {"type": "string", "enum": PROVIDER_NAMES},
            "description": "Optional production provider subset. Defaults to every Super Saiyan Browser provider.",
        }
    },
    "additionalProperties": False,
}

BUNDLE_MANIFEST_INPUT_SCHEMA = _empty_schema()
ENV_CHECKLIST_INPUT_SCHEMA = _empty_schema()

SETUP_WALKTHROUGH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "client": {
            "type": "string",
            "enum": ["cursor", "codex", "claude"],
            "description": "Optional agent client hint to tailor install-skill and init-mcp commands.",
        }
    },
    "additionalProperties": False,
}

INSTALL_SKILL_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": "string", "minLength": 1, "description": "Directory that should receive the super-browser bundle."},
        "name": {"type": "string", "minLength": 1, "default": "super-browser", "description": "Installed bundle directory name."},
        "force": {"type": "boolean", "default": False, "description": "Replace an existing installed bundle."},
    },
    "additionalProperties": False,
}

INIT_MCP_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "minLength": 1, "description": "Optional MCP config JSON path to write."},
        "cwd": {"type": "string", "minLength": 1, "description": "Repository or installed bundle path for the MCP server."},
        "force": {"type": "boolean", "default": False, "description": "Overwrite an existing MCP config file."},
        "merge": {"type": "boolean", "default": False, "description": "Merge Super Saiyan Browser into an existing MCP config."},
    },
    "additionalProperties": False,
}

LIST_RUNS_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": RUN_STATUS_VALUES, "description": "Optional run status filter."},
        "limit": {"type": "integer", "minimum": 1, "default": 20, "description": "Maximum runs to return."},
        "include_details": {"type": "boolean", "default": False, "description": "Return full run payloads instead of compact summaries."},
    },
    "additionalProperties": False,
}

TOOL_INPUT_SCHEMAS = {
    "plan_browser_task": PLAN_INPUT_SCHEMA,
    "run_browser_task": RUN_INPUT_SCHEMA,
    "resume_browser_run": _run_id_schema(),
    "get_browser_run": _run_id_schema(),
    "handoff_browser_run": _run_id_schema(),
    "list_browser_runs": LIST_RUNS_INPUT_SCHEMA,
    "verify_browser_run": _run_id_schema(),
    "approve_browser_run": APPROVE_INPUT_SCHEMA,
    "deny_browser_run": DENY_INPUT_SCHEMA,
    "list_browser_providers": _empty_schema(),
    "browser_doctor": _empty_schema(),
    "production_readiness": PRODUCTION_READINESS_INPUT_SCHEMA,
    "bundle_manifest": BUNDLE_MANIFEST_INPUT_SCHEMA,
    "env_checklist": ENV_CHECKLIST_INPUT_SCHEMA,
    "setup_walkthrough": SETUP_WALKTHROUGH_INPUT_SCHEMA,
    "run_browser_live_tests": LIVE_TEST_INPUT_SCHEMA,
    "install_super_browser_skill": INSTALL_SKILL_INPUT_SCHEMA,
    "init_super_browser_mcp": INIT_MCP_INPUT_SCHEMA,
    "list_browser_profiles": _empty_schema(),
    "get_browser_profile": PROFILE_NAME_SCHEMA,
    "create_browser_profile": CREATE_PROFILE_INPUT_SCHEMA,
    "delete_browser_profile": PROFILE_NAME_SCHEMA,
}

TOOL_DESCRIPTIONS = {
    "plan_browser_task": "Build a provider routing plan for a browser/computer automation task.",
    "run_browser_task": "Create a durable Super Saiyan Browser run record.",
    "resume_browser_run": "Resume a planned, approved, blocked, or failed run when policy allows; may execute an already approved provider action.",
    "get_browser_run": "Return a saved run by id without executing it.",
    "handoff_browser_run": "Return a compact run handoff package for another agent without executing it.",
    "list_browser_runs": "List saved runs without executing them.",
    "verify_browser_run": "Verify run artifacts and report confidence.",
    "approve_browser_run": "Record approval for a run that is awaiting approval; execute=true may execute the approved provider action immediately.",
    "deny_browser_run": "Record denial for a run that is awaiting approval.",
    "list_browser_providers": "List provider capabilities.",
    "browser_doctor": "Check provider env vars and local CLI readiness.",
    "production_readiness": "Return a hard production-readiness gate with missing env vars, uncertified workflow classes, and provider blockers.",
    "bundle_manifest": "Return a hashed inventory of the installed Super Saiyan Browser bundle for agent handoff and release audits.",
    "env_checklist": "Return required and optional Super Saiyan Browser environment variable setup without secret values.",
    "setup_walkthrough": "Return a step-by-step install walkthrough with signup links, skills, MCP, and doctor commands.",
    "run_browser_live_tests": "Run gated local/provider live tests and return artifact evidence.",
    "install_super_browser_skill": "Install or describe a self-contained Super Saiyan Browser skill/plugin bundle for another agent.",
    "init_super_browser_mcp": "Generate, write, or merge MCP config for the Super Saiyan Browser server.",
    "list_browser_profiles": "List named persistent browser profiles.",
    "get_browser_profile": "Return one named browser profile.",
    "create_browser_profile": "Create a named persistent browser profile.",
    "delete_browser_profile": "Delete a named persistent browser profile.",
}

TOOL_ANNOTATIONS = {
    "plan_browser_task": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "run_browser_task": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    "resume_browser_run": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "get_browser_run": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "handoff_browser_run": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "list_browser_runs": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "verify_browser_run": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "approve_browser_run": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "deny_browser_run": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    "list_browser_providers": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "browser_doctor": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "production_readiness": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "bundle_manifest": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "env_checklist": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "setup_walkthrough": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "run_browser_live_tests": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    "install_super_browser_skill": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
    "init_super_browser_mcp": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
    "list_browser_profiles": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "get_browser_profile": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "create_browser_profile": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    "delete_browser_profile": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
}

TOOLS = [
    {
        "name": name,
        "description": TOOL_DESCRIPTIONS[name],
        "inputSchema": TOOL_INPUT_SCHEMAS[name],
        "annotations": TOOL_ANNOTATIONS[name],
    }
    for name in TOOL_INPUT_SCHEMAS
]


def handle_tool(name: str, args: dict[str, Any]) -> Any:
    _validate_tool_args(name, args)
    return redact(_handle_tool(name, args))


def _task_kwargs(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": args.get("url"),
        "optimize": args.get("optimize", "balanced"),
        "providers_allowed": args.get("providers_allowed"),
        "max_cost_usd": args.get("max_cost_usd"),
        "timeout_seconds": args.get("timeout_seconds"),
        "profile": args.get("profile"),
        "proxy": args.get("proxy"),
    }


def _deliberation_rounds(args: dict[str, Any]) -> int | None:
    return args.get("deliberation_rounds")


def _handle_tool(name: str, args: dict[str, Any]) -> Any:
    if name == "plan_browser_task":
        task = infer_task(args["goal"], **_task_kwargs(args))
        return build_plan(task, deliberation_rounds=_deliberation_rounds(args)).to_dict()
    if name == "run_browser_task":
        fleet_size = args.get("fleet_size")
        if fleet_size:
            return create_fleet_runs(
                args["goal"],
                fleet_size=fleet_size,
                execute=args.get("execute", True),
                **_task_kwargs(args),
            )
        run = create_run(
            args["goal"],
            execute=args.get("execute", True),
            deliberation_rounds=_deliberation_rounds(args),
            **_task_kwargs(args),
        )
        return run.to_dict()
    if name == "resume_browser_run":
        return resume_run(args["run_id"]).to_dict()
    if name == "get_browser_run":
        run = RunStore(create=False).get(args["run_id"])
        if not run:
            raise ValueError(f"Run not found: {args['run_id']}")
        return run
    if name == "handoff_browser_run":
        return build_handoff(args["run_id"])
    if name == "list_browser_runs":
        return RunStore(create=False).list(
            status=args.get("status"),
            limit=args.get("limit", 20),
            include_details=args.get("include_details", False),
        )
    if name == "verify_browser_run":
        return verify_run(args["run_id"])
    if name == "approve_browser_run":
        return approve_run(
            args["run_id"],
            approver=args.get("by", "user"),
            reason=args.get("reason", ""),
            execute=args.get("execute", False),
        ).to_dict()
    if name == "deny_browser_run":
        return deny_run(args["run_id"], denied_by=args.get("by", "user"), reason=args.get("reason", "")).to_dict()
    if name == "list_browser_providers":
        return list_providers()
    if name == "browser_doctor":
        return {"providers": provider_readiness()}
    if name == "production_readiness":
        return production_readiness(required_providers=args.get("required_providers"))
    if name == "bundle_manifest":
        return build_bundle_manifest()
    if name == "env_checklist":
        return environment_checklist()
    if name == "setup_walkthrough":
        return launch_setup(client=args.get("client"))
    if name == "run_browser_live_tests":
        return run_live_tests(args.get("provider", "local"), workflow_class=args.get("workflow_class", "default"))
    if name == "install_super_browser_skill":
        return install_skill_bundle(args.get("target"), name=args.get("name", "super-browser"), force=args.get("force", False))
    if name == "init_super_browser_mcp":
        if args.get("path"):
            return write_mcp_config(
                args["path"],
                force=args.get("force", False),
                merge=args.get("merge", False),
                cwd=args.get("cwd"),
            )
        return mcp_config(cwd=args.get("cwd"))
    if name == "list_browser_profiles":
        return [item.to_dict() for item in ProfileStore(create=False).list()]
    if name == "get_browser_profile":
        profile = ProfileStore(create=False).get(args["name"])
        if not profile:
            raise ValueError(f"Profile not found: {args['name']}")
        return profile.to_dict()
    if name == "create_browser_profile":
        profile = ProfileStore().create(
            args["name"],
            description=args.get("description", ""),
            preferred_provider=args.get("preferred_provider"),
        )
        return profile.to_dict()
    if name == "delete_browser_profile":
        deleted = ProfileStore().delete(args["name"])
        if not deleted:
            raise ValueError(f"Profile not found: {args['name']}")
        return {"deleted": args["name"]}
    raise ValueError(f"Unknown tool: {name}")


def _validate_tool_args(name: str, args: dict[str, Any]) -> None:
    if name not in TOOL_INPUT_SCHEMAS:
        raise ValueError(f"Unknown tool: {name}")
    if not isinstance(args, dict):
        raise ValueError(f"{name} arguments must be an object")
    schema = TOOL_INPUT_SCHEMAS[name]
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for field in required:
        if field not in args:
            raise ValueError(f"{name} missing required argument: {field}")
    for field, value in args.items():
        if field not in properties:
            raise ValueError(f"{name} received unsupported argument: {field}")
        _validate_value(name, field, value, properties[field])


def _validate_value(tool_name: str, field: str, value: Any, schema: dict[str, Any]) -> None:
    expected_type = schema.get("type")
    if expected_type == "string":
        if not isinstance(value, str):
            raise ValueError(f"{tool_name}.{field} must be a string")
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueError(f"{tool_name}.{field} must be at least {schema['minLength']} character(s)")
        if schema.get("minLength", 0) > 0 and not value.strip():
            raise ValueError(f"{tool_name}.{field} must contain non-whitespace text")
    elif expected_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{tool_name}.{field} must be a boolean")
    elif expected_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{tool_name}.{field} must be a number")
        if not math.isfinite(float(value)):
            raise ValueError(f"{tool_name}.{field} must be finite")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{tool_name}.{field} must be >= {schema['minimum']}")
    elif expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{tool_name}.{field} must be an integer")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{tool_name}.{field} must be >= {schema['minimum']}")
    elif expected_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"{tool_name}.{field} must be an array")
        item_schema = schema.get("items", {})
        for index, item in enumerate(value):
            _validate_value(tool_name, f"{field}[{index}]", item, item_schema)
    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(str(item) for item in schema["enum"])
        raise ValueError(f"{tool_name}.{field} must be one of: {allowed}")


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        request_id = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("JSON-RPC request must be an object")
            request_has_id = "id" in request
            request_id = request.get("id") if request_has_id else None
            method = request.get("method")
            if not isinstance(method, str):
                raise ValueError("JSON-RPC method must be a string")
            if not request_has_id:
                continue
            if method == "initialize":
                result = {
                    "protocolVersion": _protocol_version(request.get("params", {})),
                    "serverInfo": {"name": "super-browser", "version": "0.3.0"},
                    "capabilities": {"tools": {}, "resources": {}},
                    "instructions": SERVER_INSTRUCTIONS,
                }
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = _call_tool_from_params(request.get("params", {}))
            elif method == "resources/list":
                result = {"resources": list_resources()}
            elif method == "resources/read":
                result = _read_resource_from_params(request.get("params", {}))
            else:
                raise ValueError(f"Unsupported method: {method}")
            _write({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as exc:
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": redact_text(str(exc))},
                }
            )
    return 0


def list_resources() -> list[dict[str, Any]]:
    resources = []
    for uri, item in _resource_files().items():
        try:
            path = _resource_path(item)
        except ValueError:
            continue
        if not path.exists():
            continue
        resources.append(
            {
                "uri": uri,
                "name": item["name"],
                "description": item["description"],
                "mimeType": "text/markdown",
                "size": path.stat().st_size,
            }
        )
    return resources


def read_resource(uri: str) -> dict[str, Any]:
    resources = _resource_files()
    if uri not in resources:
        raise ValueError(f"Unknown resource: {uri}")
    item = resources[uri]
    path = _resource_path(item)
    if not path.exists():
        raise ValueError(f"Resource not found: {uri}")
    text = redact_text(path.read_text(encoding="utf-8"))
    return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}


def _read_resource_from_params(params: Any) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise ValueError("resources/read params must be an object")
    uri = params.get("uri")
    if not isinstance(uri, str):
        raise ValueError("resources/read.uri must be a string")
    if not uri.strip():
        raise ValueError("resources/read.uri must contain non-whitespace text")
    return read_resource(uri)


def _resource_path(item: dict[str, str]) -> Path:
    root = _resource_root()
    if root is None:
        raise ValueError("Super Saiyan Browser resource docs are unavailable; set SUPER_BROWSER_REPO_ROOT to a Super Saiyan Browser repository or installed bundle")
    path = (root / item["path"]).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Resource path escapes Super Saiyan Browser root: {item['path']}") from exc
    return path


def _resource_files() -> dict[str, dict[str, str]]:
    root = _resource_root()
    if root is None:
        return {}
    resources = dict(RESOURCE_FILES)
    for skill_path in sorted((root / "skills").glob("*/SKILL.md")):
        skill_name = skill_path.parent.name
        resources[f"super-browser://skills/{skill_name}"] = {
            "path": str(skill_path.relative_to(root)),
            "name": skill_name,
            "description": f"Specialist skill documentation for {skill_name}.",
        }
    return resources


def _resource_root() -> Path | None:
    configured = os.environ.get("SUPER_BROWSER_REPO_ROOT")
    if configured:
        root = Path(configured).expanduser().resolve()
        return root if is_super_browser_root(root) else None
    return discover_repo_root(Path(__file__).resolve())


def _call_tool_result(name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = handle_tool(name, args)
    except ValueError as exc:
        if str(exc).startswith("Unknown tool:"):
            raise
        return _tool_call_error(str(exc), error_type=exc.__class__.__name__)
    except Exception as exc:
        return _tool_call_error(str(exc), error_type=exc.__class__.__name__)
    return {
        "content": [{"type": "text", "text": safe_json_dumps(payload)}],
        "structuredContent": payload,
    }


def _call_tool_from_params(params: Any) -> dict[str, Any]:
    if not isinstance(params, dict):
        return _tool_call_error("tools/call params must be an object")
    name = params.get("name")
    if not isinstance(name, str):
        return _tool_call_error("tools/call.name must be a string")
    if not name.strip():
        return _tool_call_error("tools/call.name must contain non-whitespace text")
    return _call_tool_result(name, params.get("arguments", {}))


def _tool_call_error(message: str, *, error_type: str = "ValueError") -> dict[str, Any]:
    payload = {"error": redact_text(message), "error_type": error_type}
    return {
        "content": [{"type": "text", "text": safe_json_dumps(payload)}],
        "structuredContent": payload,
        "isError": True,
    }


def _protocol_version(params: dict[str, Any]) -> str:
    requested = params.get("protocolVersion") if isinstance(params, dict) else None
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return DEFAULT_PROTOCOL_VERSION


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
