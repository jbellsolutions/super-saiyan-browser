from __future__ import annotations

import hashlib
import os
import re
from typing import Any
from urllib.parse import urlparse

from .models import TaskSpec

DECODO_DEFAULT_PORTS = tuple(range(10001, 10011))


def decodo_ports() -> tuple[int, ...]:
    raw = os.environ.get("DECODO_PORTS", "").strip()
    if not raw:
        return DECODO_DEFAULT_PORTS
    ports: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ports.append(int(part))
    return tuple(ports) or DECODO_DEFAULT_PORTS


def _parse_decodo_base() -> dict[str, str] | None:
    raw = os.environ.get("DECODO_PROXY", "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if not parsed.hostname:
        return None
    username = parsed.username or os.environ.get("DECODO_USER", "").strip()
    password = parsed.password or os.environ.get("DECODO_PASSWORD", "").strip()
    if not username or not password:
        return None
    scheme = parsed.scheme or "http"
    host = parsed.hostname
    return {"scheme": scheme, "host": host, "username": username, "password": password}


def sticky_port_for_key(key: str, ports: tuple[int, ...] | None = None) -> int:
    port_list = ports or decodo_ports()
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(port_list)
    return port_list[index]


def build_decodo_proxy_url(*, profile_name: str | None = None, port: int | None = None) -> str | None:
    base = _parse_decodo_base()
    if not base:
        return None
    chosen_port = port if port is not None else sticky_port_for_key(profile_name or "default")
    auth = f"{base['username']}:{base['password']}"
    return f"{base['scheme']}://{auth}@{base['host']}:{chosen_port}"


def resolve_proxy_url(task: TaskSpec, *, fleet_index: int | None = None) -> str | None:
    explicit = (task.proxy or "").strip()
    if explicit and explicit.lower() not in {"decodo", "auto", "sticky"}:
        return _validate_proxy_url(explicit)
    if explicit.lower() in {"decodo", "auto", "sticky"} or os.environ.get("DECODO_PROXY"):
        key = task.profile or task.goal
        if fleet_index is not None:
            key = f"{key}:fleet:{fleet_index}"
        return build_decodo_proxy_url(profile_name=key)
    return None


def playwright_proxy_settings(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    if not parsed.hostname:
        return None
    settings: dict[str, str] = {"server": f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port or 80}"}
    if parsed.username:
        settings["username"] = parsed.username
    if parsed.password:
        settings["password"] = parsed.password
    return settings


def proxy_dict_for_requests(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def _validate_proxy_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https", "socks5", "socks5h"}:
        raise ValueError("proxy must use http, https, socks5, or socks5h scheme")
    if not parsed.hostname:
        raise ValueError("proxy URL must include a host")
    if re.search(r"\s", value):
        raise ValueError("proxy URL must not contain whitespace")
    return value
