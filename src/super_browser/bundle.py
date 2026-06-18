from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path
from typing import Any

from .providers import PROVIDERS
from .redaction import redact, safe_json_dumps
from .setup_helpers import IGNORED_BUNDLE_NAMES, IGNORED_BUNDLE_PATTERNS, discover_repo_root, is_super_browser_root


MANIFEST_FILENAME = "super-browser-manifest.json"
MANIFEST_SCHEMA_VERSION = 1

REQUIRED_BUNDLE_PATHS = [
    "README.md",
    "SKILL.md",
    ".codex-plugin/plugin.json",
    ".mcp.json",
    "scripts/super-browser",
    "scripts/verify-super-browser",
    "mcp/super-browser-server",
    "references/provider-matrix.md",
    "references/routing-playbook.md",
    "references/cost-model.md",
    "references/security-and-approval-policy.md",
    "references/live-test-matrix.md",
    "src/super_browser/cli.py",
    "src/super_browser/mcp_server.py",
    "src/super_browser/providers.py",
    "src/super_browser/production.py",
]

ENTRYPOINTS = {
    "cli": "scripts/super-browser",
    "mcp_server": "mcp/super-browser-server",
    "verifier": "scripts/verify-super-browser",
}

MCP_TOOL_NAMES = [
    "plan_browser_task",
    "run_browser_task",
    "resume_browser_run",
    "get_browser_run",
    "handoff_browser_run",
    "list_browser_runs",
    "verify_browser_run",
    "approve_browser_run",
    "deny_browser_run",
    "list_browser_providers",
    "browser_doctor",
    "production_readiness",
    "bundle_manifest",
    "env_checklist",
    "setup_walkthrough",
    "run_browser_live_tests",
    "install_super_browser_skill",
    "init_super_browser_mcp",
    "list_browser_profiles",
    "get_browser_profile",
    "create_browser_profile",
    "delete_browser_profile",
]

RESOURCE_URIS = [
    "super-browser://README",
    "super-browser://SKILL",
    "super-browser://references/provider-matrix",
    "super-browser://references/routing-playbook",
    "super-browser://references/cost-model",
    "super-browser://references/security-and-approval-policy",
    "super-browser://references/live-test-matrix",
    "super-browser://docs/setup-walkthrough",
    "super-browser://docs/agent-quickstart",
]


def build_bundle_manifest(root: str | Path | None = None) -> dict[str, Any]:
    bundle_root = _resolve_bundle_root(root)
    file_entries = _file_entries(bundle_root)
    required_paths = _required_paths(bundle_root)
    missing_required = [path for path, item in required_paths.items() if not item["present"]]
    manifest = {
        "type": "super_browser_bundle_manifest",
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "status": "ok" if not missing_required else "incomplete",
        "root": str(bundle_root),
        "manifest_filename": MANIFEST_FILENAME,
        "plugin": "super-browser",
        "providers": list(PROVIDERS.keys()),
        "skills": _skill_names(bundle_root),
        "mcp_tools": MCP_TOOL_NAMES,
        "resources": _resource_uris(bundle_root),
        "entrypoints": _entrypoints(bundle_root),
        "required_paths": required_paths,
        "missing_required_paths": missing_required,
        "ignored_bundle_names": sorted(IGNORED_BUNDLE_NAMES),
        "ignored_bundle_patterns": sorted(IGNORED_BUNDLE_PATTERNS),
        "files": file_entries,
        "file_count": len(file_entries),
        "total_bytes": sum(int(item["bytes"]) for item in file_entries),
    }
    return redact(manifest)


def write_bundle_manifest(root: str | Path | None = None, path: str | Path | None = None) -> dict[str, Any]:
    bundle_root = _resolve_bundle_root(root)
    manifest = build_bundle_manifest(bundle_root)
    output_path = Path(path).expanduser().resolve() if path else bundle_root / MANIFEST_FILENAME
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(safe_json_dumps(manifest) + "\n", encoding="utf-8")
    return {
        **manifest,
        "written": True,
        "manifest_path": str(output_path),
    }


def _resolve_bundle_root(root: str | Path | None) -> Path:
    if root is None:
        discovered = discover_repo_root()
        if discovered is None:
            raise ValueError("Super Saiyan Browser source root or installed bundle was not found.")
        return discovered
    bundle_root = Path(root).expanduser().resolve()
    if not is_super_browser_root(bundle_root):
        raise ValueError(f"Path is not a Super Saiyan Browser repository or installed bundle: {bundle_root}")
    return bundle_root


def _file_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(_iter_manifest_files(root), key=lambda item: _relative_path(root, item)):
        relative = _relative_path(root, path)
        stat = path.stat()
        entries.append(
            {
                "path": relative,
                "bytes": stat.st_size,
                "sha256": _sha256(path),
                "executable": os.access(path, os.X_OK),
            }
        )
    return entries


def _iter_manifest_files(root: Path):
    for current_root, dir_names, file_names in os.walk(root):
        current = Path(current_root)
        dir_names[:] = [
            name
            for name in dir_names
            if not _should_ignore_name(name) and not (current / name).is_symlink()
        ]
        for name in file_names:
            path = current / name
            if path.is_symlink() or _should_ignore_name(name):
                continue
            if _relative_path(root, path) == MANIFEST_FILENAME:
                continue
            yield path


def _should_ignore_name(name: str) -> bool:
    if name in IGNORED_BUNDLE_NAMES:
        return True
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in IGNORED_BUNDLE_PATTERNS)


def _required_paths(root: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for relative in REQUIRED_BUNDLE_PATHS:
        path = root / relative
        rows[relative] = _path_summary(path)
    return rows


def _entrypoints(root: Path) -> dict[str, dict[str, Any]]:
    return {name: _path_summary(root / relative) for name, relative in ENTRYPOINTS.items()}


def _path_summary(path: Path) -> dict[str, Any]:
    present = path.is_file()
    summary = {
        "present": present,
        "executable": bool(present and os.access(path, os.X_OK)),
    }
    if present:
        stat = path.stat()
        summary["bytes"] = stat.st_size
        summary["sha256"] = _sha256(path)
    return summary


def _skill_names(root: Path) -> list[str]:
    skills_root = root / "skills"
    if not skills_root.is_dir():
        return []
    return sorted(path.parent.name for path in skills_root.glob("*/SKILL.md") if path.is_file())


def _resource_uris(root: Path) -> list[str]:
    uris = list(RESOURCE_URIS)
    uris.extend(f"super-browser://skills/{skill_name}" for skill_name in _skill_names(root))
    return uris


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
