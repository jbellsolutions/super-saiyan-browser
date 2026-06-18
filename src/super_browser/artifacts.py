from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def fingerprint_path(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"bytes": file_path.stat().st_size, "sha256": digest.hexdigest()}


def annotate_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    annotated = dict(artifact)
    path = annotated.get("path")
    if not path:
        return annotated
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        return annotated
    fingerprint = fingerprint_path(file_path)
    annotated.setdefault("bytes", fingerprint["bytes"])
    annotated["sha256"] = fingerprint["sha256"]
    return annotated


def annotate_artifacts(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [annotate_artifact(artifact) for artifact in artifacts]
