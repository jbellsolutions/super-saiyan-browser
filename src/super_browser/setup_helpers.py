from __future__ import annotations

import json
import os
import shutil
import fnmatch
import sys
from pathlib import Path
from typing import Any


IGNORED_BUNDLE_NAMES = {
    ".coverage",
    ".DS_Store",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".super-browser",
    ".tox",
    ".venv",
    ".git",
    "__pycache__",
    "build",
    "dist",
    "env",
    "htmlcov",
    "node_modules",
    "venv",
}

IGNORED_BUNDLE_PATTERNS = {
    ".env",
    ".env.*",
    "*.db",
    "*.log",
    "*.pyc",
    "*.pyo",
    "*.sqlite",
    "*.sqlite3",
    "coverage.xml",
}


def repo_root() -> Path:
    root = _repo_root_or_none()
    if root:
        return root
    raise ValueError(
        "Super Saiyan Browser source root was not found. Set SUPER_BROWSER_REPO_ROOT to a Super Saiyan Browser repository or installed bundle "
        "when installing the skill/plugin bundle."
    )


def discover_repo_root(start: str | Path | None = None) -> Path | None:
    if start is None:
        start = Path(__file__).resolve()
    return _discover_repo_root(Path(start).expanduser().resolve()) or _packaged_asset_root_or_none()


def is_super_browser_root(path: str | Path) -> bool:
    return _looks_like_super_browser_root(Path(path).expanduser().resolve())


def mcp_config(cwd: str | Path | None = None) -> dict[str, Any]:
    if cwd is None:
        root = _repo_root_or_none()
        if root is None:
            return _package_mcp_config()
        if _is_packaged_asset_root(root):
            return _package_mcp_config(root)
    else:
        root = Path(cwd).expanduser().resolve()
    root = _validated_mcp_root(root)
    server = root / "mcp" / "super-browser-server"
    return {
        "mcpServers": {
            "super-browser": {
                "cwd": str(root),
                "command": str(server),
                "args": [],
                "env": {"SUPER_BROWSER_REPO_ROOT": str(root)},
            }
        }
    }


def write_mcp_config(path: str | Path, force: bool = False, merge: bool = False, cwd: str | Path | None = None) -> dict[str, Any]:
    from .redaction import safe_json_dumps

    output_path = Path(path).expanduser().resolve()
    if force and merge:
        raise ValueError("Use either --force or --merge for init-mcp, not both.")
    existed = output_path.exists()
    if output_path.exists() and not force and not merge:
        raise ValueError(f"MCP config already exists: {output_path}. Pass --merge to preserve other servers or --force to overwrite it.")
    payload = _merged_mcp_config(output_path, cwd=cwd) if merge else mcp_config(cwd=cwd)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(safe_json_dumps(payload) + "\n", encoding="utf-8")
    return {"status": "merged" if merge and existed else "written", "path": str(output_path), "config": payload}


def install_skill_bundle(target: str | Path | None, name: str = "super-browser", force: bool = False) -> dict[str, Any]:
    root = _repo_root_or_none()
    if root is None:
        if target is None:
            return {
                "status": "source_unavailable",
                "plugin": "super-browser",
                "target_required": True,
                "example": "SUPER_BROWSER_REPO_ROOT=/path/to/super-browser super-browser install-skill --target ~/.codex/skills",
                "message": (
                    "install-skill needs a Super Saiyan Browser source repository, installed bundle, or packaged asset tree. "
                    "This package installation did not include the plugin assets."
                ),
            }
        raise ValueError(
            "install-skill needs a Super Saiyan Browser source repository or installed bundle. Set SUPER_BROWSER_REPO_ROOT to that path, "
            "or run install-skill from the repository checkout."
        )
    if target is None:
        return {
            "status": "dry_run",
            "plugin": "super-browser",
            "source": str(root),
            "target_required": True,
            "example": "super-browser install-skill --target ~/.codex/skills",
            "copies_to": "<target>/super-browser",
        }

    target_root = Path(target).expanduser().resolve()
    bundle_name = name.strip()
    if not bundle_name or bundle_name in {".", ".."} or any(separator in bundle_name for separator in ("/", "\\")):
        raise ValueError("skill bundle name must be a simple directory name")
    destination = (target_root / bundle_name).resolve()
    if destination.parent != target_root:
        raise ValueError("skill bundle destination must stay inside the target directory")
    _ensure_not_inside_source(root, destination)
    if destination.exists() and not force:
        raise ValueError(f"Skill bundle already exists: {destination}. Pass --force to update it.")
    target_root.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(root, destination, ignore=_copy_ignore)
    _make_entrypoints_executable(destination)
    from .bundle import write_bundle_manifest

    manifest = write_bundle_manifest(destination)
    return {
        "status": "installed",
        "plugin": "super-browser",
        "source": str(root),
        "installed_path": str(destination),
        "skill_file": str(destination / "SKILL.md"),
        "cli": str(destination / "scripts" / "super-browser"),
        "mcp_server": str(destination / "mcp" / "super-browser-server"),
        "bundle_manifest": manifest["manifest_path"],
        "mcp_config": mcp_config(cwd=destination),
    }


def _ensure_not_inside_source(source: Path, destination: Path) -> None:
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("install target must be outside the Super Saiyan Browser repository")
    try:
        source.relative_to(destination)
    except ValueError:
        return
    raise ValueError("install destination must not contain the Super Saiyan Browser repository")


def _validated_mcp_root(cwd: str | Path | None = None) -> Path:
    if cwd is None:
        root = repo_root()
    else:
        if isinstance(cwd, str) and not cwd.strip():
            raise ValueError("MCP cwd must be a non-empty path")
        root = Path(cwd).expanduser().resolve()
    server = root / "mcp" / "super-browser-server"
    if not root.is_dir() or not _looks_like_super_browser_root(root) or not server.is_file() or not os.access(server, os.X_OK):
        raise ValueError(
            "MCP cwd must point to a Super Saiyan Browser repository or installed bundle containing executable mcp/super-browser-server: "
            f"{root}"
        )
    return root


def _repo_root_or_none() -> Path | None:
    configured = os.environ.get("SUPER_BROWSER_REPO_ROOT")
    if configured:
        root = Path(configured).expanduser().resolve()
        return root if _looks_like_super_browser_root(root) else None
    return discover_repo_root(Path(__file__).resolve())


def _discover_repo_root(start: Path) -> Path | None:
    candidates = [start if start.is_dir() else start.parent, *start.parents]
    for candidate in candidates:
        if _looks_like_super_browser_root(candidate):
            return candidate.resolve()
    return None


def _looks_like_super_browser_root(path: Path) -> bool:
    return (
        (path / "SKILL.md").is_file()
        and (path / "README.md").is_file()
        and (path / "skills").is_dir()
        and (path / "references").is_dir()
        and (path / "mcp" / "super-browser-server").is_file()
        and (path / "scripts" / "super-browser").is_file()
    )


def _packaged_asset_root_or_none() -> Path | None:
    candidates = [
        Path(sys.prefix) / "share" / "super-browser",
        Path(sys.base_prefix) / "share" / "super-browser",
    ]
    for candidate in candidates:
        if _looks_like_super_browser_root(candidate):
            return candidate.resolve()
    return None


def _is_packaged_asset_root(root: Path) -> bool:
    packaged = _packaged_asset_root_or_none()
    return bool(packaged and root.resolve() == packaged)


def _package_mcp_config(root: Path | None = None) -> dict[str, Any]:
    env = {"SUPER_BROWSER_REPO_ROOT": str(root)} if root else {}
    return {
        "mcpServers": {
            "super-browser": {
                "cwd": str(Path.cwd().resolve()),
                "command": sys.executable,
                "args": ["-m", "super_browser.mcp_server"],
                "env": env,
            }
        }
    }


def _merged_mcp_config(path: Path, cwd: str | Path | None = None) -> dict[str, Any]:
    if not path.exists():
        return mcp_config(cwd=cwd)
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"MCP config is not valid JSON: {path}") from exc
    if not isinstance(existing, dict):
        raise ValueError("MCP config root must be a JSON object")
    servers = existing.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("MCP config mcpServers must be a JSON object")
    servers["super-browser"] = mcp_config(cwd=cwd)["mcpServers"]["super-browser"]
    return existing


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        if _should_ignore_bundle_name(name) or (Path(directory) / name).is_symlink():
            ignored.add(name)
    return ignored


def _should_ignore_bundle_name(name: str) -> bool:
    if name in IGNORED_BUNDLE_NAMES:
        return True
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in IGNORED_BUNDLE_PATTERNS)


def _make_entrypoints_executable(destination: Path) -> None:
    for path in [destination / "scripts" / "super-browser", destination / "mcp" / "super-browser-server"]:
        if not path.exists():
            continue
        path.chmod(path.stat().st_mode | 0o755)
