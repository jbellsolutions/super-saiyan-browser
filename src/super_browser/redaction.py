from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any


REDACTED = "[REDACTED]"

_SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "auth_header",
    "api_key",
    "bearer_token",
    "client_secret",
    "cookie",
    "password",
    "private_key",
    "proxy_authorization",
    "refresh_token",
    "secret",
    "session_cookie",
    "session_token",
)

_SENSITIVE_KEY_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "credential",
    "credentials",
    "id_token",
    "jwt",
    "key",
    "token",
    "x-api-key",
}

_QUERY_SECRET_RE = re.compile(
    r"(?P<prefix>[?&](?:access_token|api_key|apikey|auth|client_secret|code|id_token|key|password|refresh_token|secret|token)=)"
    r"(?P<value>[^&#\s]+)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_BASIC_RE = re.compile(r"\bBasic\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_URL_USERINFO_RE = re.compile(
    r"\b(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<userinfo>[^/?#\s@]+@)",
    re.IGNORECASE,
)
_ENV_ASSIGNMENT_RE = re.compile(
    r"\b(?P<name>[A-Z0-9_]*(?:API_KEY|AUTHORIZATION|COOKIE|PASSWORD|SECRET|TOKEN)[A-Z0-9_]*)="
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s]+)",
    re.IGNORECASE,
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


def redact(value: Any) -> Any:
    """Return a copy of value with high-risk secrets removed."""
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            redacted[key] = REDACTED if _is_sensitive_key(str(key)) else redact(item)
        return redacted
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes):
        try:
            return redact_text(value.decode("utf-8"))
        except UnicodeDecodeError:
            return value
    return value


def redact_headers(headers: Mapping[str, Any]) -> dict[str, Any]:
    return {key: REDACTED if _is_sensitive_key(str(key)) else redact(value) for key, value in headers.items()}


def redact_text(text: str | None) -> str | None:
    if text is None:
        return None
    redacted = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    redacted = _BASIC_RE.sub(f"Basic {REDACTED}", redacted)
    redacted = _URL_USERINFO_RE.sub(lambda match: f"{match.group('scheme')}{REDACTED}@", redacted)
    redacted = _JWT_RE.sub(REDACTED, redacted)
    redacted = _ENV_ASSIGNMENT_RE.sub(lambda match: f"{match.group('name')}={REDACTED}", redacted)
    redacted = _QUERY_SECRET_RE.sub(lambda match: f"{match.group('prefix')}{REDACTED}", redacted)
    return redacted


def safe_json_dumps(payload: Any) -> str:
    return json.dumps(redact(payload), indent=2, sort_keys=True, default=str)


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if normalized in _SENSITIVE_KEY_NAMES:
        return True
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
