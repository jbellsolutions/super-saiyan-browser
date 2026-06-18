from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .models import utc_now
from .redaction import redact


def default_state_dir() -> Path:
    return Path(os.environ.get("SUPER_BROWSER_STATE_DIR", ".super-browser"))


@dataclass
class BrowserProfile:
    name: str
    description: str = ""
    preferred_provider: str | None = None
    provider_ids: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BrowserProfile":
        return cls(
            name=str(payload["name"]),
            description=str(payload.get("description") or ""),
            preferred_provider=payload.get("preferred_provider"),
            provider_ids=dict(payload.get("provider_ids") or {}),
            metadata=dict(payload.get("metadata") or {}),
            created_at=str(payload.get("created_at") or utc_now()),
            updated_at=str(payload.get("updated_at") or utc_now()),
        )


class ProfileStore:
    def __init__(self, path: str | Path | None = None, create: bool = True):
        if path:
            self.path = Path(path)
            if create:
                self.path.parent.mkdir(parents=True, exist_ok=True)
        else:
            state_dir = default_state_dir()
            if create:
                state_dir.mkdir(parents=True, exist_ok=True)
            self.path = state_dir / "profiles.sqlite"
        if create:
            self._init()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    name TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def create(
        self,
        name: str,
        *,
        description: str = "",
        preferred_provider: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BrowserProfile:
        normalized = _validate_profile_name(name)
        if self.get(normalized):
            raise ValueError(f"Profile already exists: {normalized}")
        profile = BrowserProfile(
            name=normalized,
            description=description.strip(),
            preferred_provider=preferred_provider,
            metadata=metadata or {},
        )
        self._save(profile)
        return profile

    def _save(self, profile: BrowserProfile) -> None:
        profile.updated_at = utc_now()
        payload = json.dumps(redact(profile.to_dict()), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO profiles (name, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (profile.name, payload, profile.created_at, profile.updated_at),
            )

    def get(self, name: str) -> BrowserProfile | None:
        if not self.path.exists():
            return None
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT payload FROM profiles WHERE name = ?", (name,)).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return None
            raise
        if not row:
            return None
        return BrowserProfile.from_dict(json.loads(row[0]))

    def list(self) -> list[BrowserProfile]:
        if not self.path.exists():
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT payload FROM profiles ORDER BY name ASC").fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return []
            raise
        return [BrowserProfile.from_dict(json.loads(row[0])) for row in rows]

    def delete(self, name: str) -> bool:
        if not self.path.exists():
            return False
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM profiles WHERE name = ?", (name,))
            return cursor.rowcount > 0

    def bind_provider_id(self, name: str, provider: str, provider_id: str) -> BrowserProfile:
        profile = self.get(name)
        if not profile:
            raise ValueError(f"Profile not found: {name}")
        profile.provider_ids[provider] = provider_id
        self._save(profile)
        return profile

    def resolve_provider_id(self, name: str, provider: str) -> str | None:
        profile = self.get(name)
        if not profile:
            return None
        return profile.provider_ids.get(provider)


def _validate_profile_name(name: str) -> str:
    normalized = (name or "").strip()
    if not normalized:
        raise ValueError("profile name must be a non-empty string")
    if any(char.isspace() for char in normalized):
        raise ValueError("profile name must not contain whitespace")
    if len(normalized) > 64:
        raise ValueError("profile name must be 64 characters or fewer")
    return normalized
