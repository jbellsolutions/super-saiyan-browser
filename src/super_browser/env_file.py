from __future__ import annotations

import os
import re
from pathlib import Path

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")


def find_repo_root() -> Path | None:
    configured = os.environ.get("SUPER_BROWSER_REPO_ROOT", "").strip()
    if configured:
        return Path(configured)
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").exists() or (candidate / ".env.example").exists():
            return candidate
    return None


def load_env_file(path: Path, *, override: bool = False) -> list[str]:
    if not path.is_file():
        return []
    loaded: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.match(raw_line)
        if not match:
            continue
        name, value = match.group(1), _unquote(match.group(2))
        if override or not os.environ.get(name):
            os.environ[name] = value
            loaded.append(name)
    return loaded


def merge_env_file(path: Path, updates: dict[str, str]) -> list[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines: list[str] = []
    if path.is_file():
        existing_lines = path.read_text(encoding="utf-8").splitlines()
    written: list[str] = []
    remaining = dict(updates)
    output: list[str] = []
    for line in existing_lines:
        match = _ENV_LINE.match(line)
        if match and match.group(1) in remaining:
            name = match.group(1)
            output.append(f"{name}={_quote(remaining.pop(name))}")
            written.append(name)
        else:
            output.append(line)
    for name, value in remaining.items():
        if output and output[-1].strip():
            output.append("")
        output.append(f"{name}={_quote(value)}")
        written.append(name)
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    return written


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _quote(value: str) -> str:
    if not value:
        return '""'
    if re.search(r"[\s#\"'\\]", value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value
