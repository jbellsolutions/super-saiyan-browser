from __future__ import annotations

import json
import time
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .zones import brightdata_config

ErrorClass = Literal["fatal", "retryable", "auth", "budget", "empty"]


class BrightDataError(Exception):
    def __init__(self, message: str, *, error_class: ErrorClass = "fatal", status_code: int | None = None):
        super().__init__(message)
        self.error_class = error_class
        self.status_code = status_code


def classify_brightdata_error(exc: Exception, payload: dict | str | None = None) -> ErrorClass:
    status = getattr(exc, "code", None) if isinstance(exc, HTTPError) else None
    text = str(exc).lower()
    if payload and isinstance(payload, dict):
        text = f"{text} {json.dumps(payload).lower()}"
    if status in {401, 403} or "unauthorized" in text or "authentication" in text:
        return "auth"
    if status == 429 or "rate limit" in text or "too many" in text:
        return "retryable"
    if status in {408, 502, 503, 504} or isinstance(exc, URLError):
        return "retryable"
    if status == 402 or "insufficient" in text or "missing)" in text:
        return "budget"
    if "not match any records" in text or "empty" in text:
        return "empty"
    return "fatal"


class BrightDataClient:
    def __init__(self, *, api_key: str | None = None, api_base: str | None = None, timeout_seconds: int = 120):
        cfg = brightdata_config()
        self.api_key = api_key or cfg.api_key
        self.api_base = (api_base or cfg.api_base).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict | list | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        if not self.api_key:
            raise BrightDataError("BRIGHTDATA_API_KEY is not configured", error_class="auth")
        url = f"{self.api_base}{path}"
        if params:
            query = "&".join(f"{key}={value}" for key, value in params.items())
            url = f"{url}?{query}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "super-browser/1.0",
        }
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
            except Exception:
                pass
            payload = _safe_json_loads(detail)
            error_class = classify_brightdata_error(exc, payload if isinstance(payload, dict) else detail)
            message = detail or str(exc.reason)
            raise BrightDataError(message, error_class=error_class, status_code=exc.code) from exc
        except URLError as exc:
            raise BrightDataError(str(exc.reason), error_class="retryable") from exc
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def request_text(
        self,
        method: str,
        path: str,
        *,
        body: dict | list | None = None,
        params: dict[str, str] | None = None,
    ) -> str:
        if not self.api_key:
            raise BrightDataError("BRIGHTDATA_API_KEY is not configured", error_class="auth")
        url = f"{self.api_base}{path}"
        if params:
            query = "&".join(f"{key}={value}" for key, value in params.items())
            url = f"{url}?{query}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "super-browser/1.0",
        }
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            error_class = classify_brightdata_error(exc, detail)
            raise BrightDataError(detail or str(exc.reason), error_class=error_class, status_code=exc.code) from exc
        except URLError as exc:
            raise BrightDataError(str(exc.reason), error_class="retryable") from exc

    def unlock_request(self, *, zone: str, url: str, data_format: str = "markdown", country: str | None = None) -> str:
        body: dict[str, Any] = {"zone": zone, "url": url, "format": "raw", "data_format": data_format}
        if country:
            body["country"] = country.lower()
        return self.request_text("POST", "/request", body=body)

    def poll_snapshot(self, snapshot_id: str, *, attempts: int = 20, delay_seconds: float = 2.0) -> dict[str, Any]:
        latest: dict[str, Any] = {}
        for _ in range(attempts):
            latest = self.request_json("GET", f"/datasets/v3/progress/{snapshot_id}")
            status = str(latest.get("status") or "").lower()
            if status in {"ready", "done", "complete", "completed"}:
                return latest
            if status in {"failed", "error", "cancelled", "canceled"}:
                raise BrightDataError(f"Snapshot {snapshot_id} failed with status={status}", error_class="fatal")
            if delay_seconds:
                time.sleep(delay_seconds)
        raise BrightDataError(f"Snapshot {snapshot_id} did not become ready in time", error_class="retryable")

    def download_snapshot(self, snapshot_id: str, *, format: str = "json") -> Any:
        return self.request_json("GET", f"/datasets/v3/snapshot/{snapshot_id}", params={"format": format})


def _safe_json_loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return raw
