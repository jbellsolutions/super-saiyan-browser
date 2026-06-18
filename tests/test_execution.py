import json
import hashlib
import math
import os
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import sys

from super_browser.models import ExecutionResult, action_fingerprint_from_plan, plan_fingerprint, utc_now
from super_browser.runtime import (
    DEFAULT_EXECUTION_LEASE_SECONDS,
    LONG_RUNNING_EXECUTION_LEASE_SECONDS,
    _execute_run,
    _execution_lease_seconds,
    _run_from_payload,
    create_run,
    resume_run,
)
from super_browser.adapters import execute_plan, _task_prompt
from super_browser.router import build_plan, infer_task
from super_browser.store import RunStore
from super_browser.verifier import verify_run


class _JsonHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok": true, "name": "fixture"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class _RedirectToLinkLocalHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://169.254.169.254/latest/meta-data")
        self.end_headers()

    def log_message(self, format, *args):
        return


class _FakeHttpResponse:
    status = 200

    class _Headers:
        def items(self):
            return [("Content-Type", "application/json")]

    headers = _Headers()

    def read(self):
        return b'{"ok": true}'

    def getcode(self):
        return self.status


def _approval_context_for(plan):
    return {
        "approval_id": "approval_test",
        "status": "approved",
        "required_before": "provider_execution",
        "action_fingerprint": action_fingerprint_from_plan(plan),
        "decided_at": utc_now(),
        "decided_by": "test",
        "plan_sha256": plan_fingerprint(plan),
    }


def _artifact_path(state_dir: str, run_id: str, filename: str) -> str:
    path = Path(state_dir) / "artifacts" / run_id / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


class ExecutionTests(unittest.TestCase):
    def test_draft_only_task_prompt_contains_external_write_stop_instruction(self):
        task = infer_task("Draft a LinkedIn comment, put it in the box, but do not publish", url="https://example.com")
        prompt = _task_prompt(task)
        self.assertIn("SAFETY: This is a draft-only run", prompt)
        self.assertIn("Do not publish, post, comment, reply, respond, message, DM, send, submit, upload", prompt)
        self.assertIn("click, tap, press, or activate any final external-write control", prompt)
        self.assertIn("Start at https://example.com.", prompt)

    def test_draft_only_task_prompt_blocks_non_text_external_state_changes(self):
        task = infer_task("Draft a CRM follow-up note about adding this product to cart but do not submit", url="https://example.com")
        prompt = _task_prompt(task)
        self.assertTrue(task.draft_only)
        self.assertFalse(task.external_write)
        self.assertIn("Do not follow, connect, like, react, vote, bookmark, save, pin, star, watch, fork, share, RSVP, attend", prompt)
        self.assertIn("Do not update CRM records, change cart/order/payment/trading/banking/payout/legal/government/health/insurance/identity/project/repository/cloud-file/sharing/integration/settings/secrets/infrastructure/billing/workspace/channel/role/moderation state, toggle notifications, archive or mark messages/email", prompt)
        self.assertIn("remove members, accept or decline invites, or change account/profile state", prompt)

    def test_draft_only_task_prompt_uses_policy_when_stored_flag_is_false(self):
        task = infer_task("Draft a LinkedIn comment, put it in the box, but do not publish", url="https://example.com")
        task.draft_only = False

        prompt = _task_prompt(task)

        self.assertIn("SAFETY: This is a draft-only run", prompt)
        self.assertIn("Do not publish, post, comment, reply, respond, message, DM, send, submit, upload", prompt)

    def test_external_write_task_prompt_ignores_stale_draft_flag(self):
        task = infer_task("Post a LinkedIn comment", url="https://example.com")
        task.draft_only = True

        prompt = _task_prompt(task)

        self.assertNotIn("SAFETY: This is a draft-only run", prompt)
        self.assertIn("SAFETY: This is an external-write run", prompt)
        self.assertIn("Perform only the exact requested external action", prompt)
        self.assertIn("Start at https://example.com. Post a LinkedIn comment", prompt)

    def test_external_write_task_prompt_uses_policy_when_stored_flag_is_false(self):
        task = infer_task("Post a LinkedIn comment", url="https://example.com")
        task.external_write = False

        prompt = _task_prompt(task)

        self.assertIn("SAFETY: This is an external-write run", prompt)
        self.assertIn("Do not perform adjacent actions such as following, connecting, liking, reacting", prompt)
        self.assertIn("Stop after the exact requested action", prompt)

    def test_authenticated_read_task_prompt_contains_no_write_boundary(self):
        task = infer_task("Use my logged in Chrome session to read private dashboard notifications")

        prompt = _task_prompt(task)

        self.assertIn("SAFETY: This is an authenticated read/navigation run", prompt)
        self.assertIn("Do not publish, post, comment, reply, message, send, submit, upload", prompt)
        self.assertIn("read private dashboard notifications", prompt)

    def test_public_read_task_prompt_contains_read_only_boundary(self):
        task = infer_task("Extract product names from https://example.com/products")

        prompt = _task_prompt(task)

        self.assertIn("SAFETY: This is a read-only run", prompt)
        self.assertIn("Navigate, search, scroll, inspect, and extract only", prompt)
        self.assertIn("Extract product names", prompt)

    def test_raw_http_execution_saves_artifacts(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            url = f"http://127.0.0.1:{server.server_port}/data.json"
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                run = create_run("Fetch this JSON endpoint through raw HTTP", url=url, providers_allowed=["decodo-http"])
                payload = run.to_dict()
                self.assertEqual(payload["status"], "complete")
                self.assertEqual(payload["plan"]["primary_provider"], "decodo-http")
                self.assertEqual(payload["verification"]["confidence"], "high")
                response_artifacts = [item for item in payload["artifacts"] if item["type"] == "http_response"]
                self.assertEqual(len(response_artifacts), 1)
                metadata_artifact = next(item for item in payload["artifacts"] if item["type"] == "metadata")
                with open(metadata_artifact["path"], encoding="utf-8") as handle:
                    metadata = json.load(handle)
                self.assertEqual(metadata["target_scope"], "loopback")
                report_artifacts = [item for item in payload["artifacts"] if item["type"] == "run_report"]
                with open(report_artifacts[0]["path"], encoding="utf-8") as handle:
                    report = json.load(handle)
                self.assertEqual(report["cost_estimate"]["primary"]["provider"], "decodo-http")
                self.assertEqual(report["cost_estimate"]["budget_status"], "no_ceiling")
                with open(response_artifacts[0]["path"], "rb") as handle:
                    body = handle.read()
                self.assertEqual(json.loads(body), {"ok": True, "name": "fixture"})
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            server.shutdown()
            server.server_close()

    def test_raw_http_execution_uses_plan_timeout(self):
        captured_timeouts = []

        def fake_open(request, timeout_seconds, proxy, redirect_handler):
            captured_timeouts.append(timeout_seconds)
            return _FakeHttpResponse()

        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            plan = build_plan(
                infer_task(
                    "Fetch this JSON endpoint through raw HTTP",
                    url="https://example.com/data.json",
                    timeout_seconds=7,
                )
            )
            with tempfile.TemporaryDirectory() as tmp:
                with patch("super_browser.adapters._open_raw_http_request", side_effect=fake_open):
                    result = execute_plan(plan, "run_timeout_contract", state_dir=Path(tmp), use_fallbacks=False)
                payload = result.to_dict()
                self.assertEqual(payload["status"], "complete")
                self.assertEqual(captured_timeouts, [7])
                self.assertIn("timeout_seconds=7", payload["verification"]["checks"])
                metadata_artifact = next(item for item in payload["artifacts"] if item["type"] == "metadata")
                with open(metadata_artifact["path"], encoding="utf-8") as handle:
                    metadata = json.load(handle)
                self.assertEqual(metadata["timeout_seconds"], 7)
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy

    def test_raw_http_blocks_redirect_to_sensitive_target_scope(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectToLinkLocalHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            url = f"http://127.0.0.1:{server.server_port}/redirect"
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                run = create_run("Fetch this JSON endpoint through raw HTTP", url=url, providers_allowed=["decodo-http"])
                payload = run.to_dict()
                self.assertEqual(payload["status"], "blocked")
                self.assertEqual(payload["plan"]["task"]["target_scope"], "loopback")
                self.assertIn("redirect to sensitive target scope was blocked", payload["verification"]["checks"])
                self.assertNotIn("http_response", [artifact["type"] for artifact in payload["artifacts"]])
                metadata_artifact = next(item for item in payload["artifacts"] if item["type"] == "metadata")
                with open(metadata_artifact["path"], encoding="utf-8") as handle:
                    metadata = json.load(handle)
                self.assertEqual(metadata["redirects"][0]["target_scope"], "link_local")
                self.assertEqual(metadata["redirects"][0]["to_url"], "http://169.254.169.254/latest/meta-data")
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            server.shutdown()
            server.server_close()

    def test_raw_http_blocks_public_hostname_resolving_to_sensitive_target_scope(self):
        def fake_getaddrinfo(host, port, type=0):
            self.assertEqual(host, "metadata.example.test")
            return [(None, None, None, None, ("169.254.169.254", port))]

        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            plan = build_plan(
                infer_task(
                    "Fetch this JSON endpoint through raw HTTP",
                    url="http://metadata.example.test/data.json",
                    providers_allowed=["decodo-http"],
                )
            )
            with tempfile.TemporaryDirectory() as tmp:
                with patch("super_browser.adapters.socket.getaddrinfo", side_effect=fake_getaddrinfo):
                    with patch("super_browser.adapters._open_raw_http_request") as open_mock:
                        result = execute_plan(plan, "run_raw_http_dns_scope_guard", state_dir=Path(tmp), use_fallbacks=False)
                payload = result.to_dict()
                self.assertEqual(payload["status"], "blocked")
                self.assertEqual(payload["provider"], "decodo-http")
                self.assertFalse(open_mock.called)
                self.assertIn("resolved target to sensitive scope was blocked", payload["verification"]["checks"])
                self.assertNotIn("http_response", [artifact["type"] for artifact in payload["artifacts"]])
                metadata_artifact = next(item for item in payload["artifacts"] if item["type"] == "metadata")
                with open(metadata_artifact["path"], encoding="utf-8") as handle:
                    metadata = json.load(handle)
                self.assertEqual(metadata["target_scope"], "public_web")
                self.assertEqual(metadata["blocked_scope"], "link_local")
                self.assertEqual(metadata["target_evidence"]["resolved_addresses"][0]["target_scope"], "link_local")
                self.assertEqual(metadata["target_evidence"]["resolved_addresses"][0]["address"], "169.254.169.254")
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy

    def test_raw_http_blocks_public_hostname_when_dns_resolution_fails(self):
        def fake_getaddrinfo(host, port, type=0):
            self.assertEqual(host, "unresolved.example.test")
            raise OSError("simulated DNS failure")

        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            plan = build_plan(
                infer_task(
                    "Fetch this JSON endpoint through raw HTTP",
                    url="http://unresolved.example.test/data.json",
                    providers_allowed=["decodo-http"],
                )
            )
            with tempfile.TemporaryDirectory() as tmp:
                with patch("super_browser.adapters.socket.getaddrinfo", side_effect=fake_getaddrinfo):
                    with patch("super_browser.adapters._open_raw_http_request") as open_mock:
                        result = execute_plan(plan, "run_raw_http_dns_failure_guard", state_dir=Path(tmp), use_fallbacks=False)
                payload = result.to_dict()
                self.assertEqual(payload["status"], "blocked")
                self.assertFalse(open_mock.called)
                self.assertIn("public target DNS resolution failed", payload["verification"]["checks"])
                metadata_artifact = next(item for item in payload["artifacts"] if item["type"] == "metadata")
                with open(metadata_artifact["path"], encoding="utf-8") as handle:
                    metadata = json.load(handle)
                self.assertEqual(metadata["blocked_scope"], "unresolved_public_web")
                self.assertEqual(metadata["target_evidence"]["resolution_error"], "simulated DNS failure")
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy

    def test_resume_blocked_target_scope_safety_stop_requires_replan(self):
        def fake_getaddrinfo(host, port, type=0):
            self.assertEqual(host, "metadata.example.test")
            return [(None, None, None, None, ("169.254.169.254", port))]

        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                with patch("super_browser.adapters.socket.getaddrinfo", side_effect=fake_getaddrinfo):
                    run = create_run(
                        "Fetch this JSON endpoint through raw HTTP",
                        url="http://metadata.example.test/data.json",
                        providers_allowed=["decodo-http"],
                    )
                self.assertEqual(run.status, "blocked")
                self.assertIn("raw_http_resolved_target_scope", [event.get("reason") for event in run.events if event.get("type") == "blocked"])

                with patch("super_browser.runtime.execute_plan") as execute_mock:
                    resumed = resume_run(run.run_id)

                payload = resumed.to_dict()
                self.assertEqual(payload["status"], "blocked")
                self.assertFalse(execute_mock.called)
                self.assertNotIn("execution_resumed", [event["type"] for event in payload["events"]])
                block_event = payload["events"][-1]
                self.assertEqual(block_event["type"], "resume_blocked")
                self.assertEqual(block_event["reason"], "non_resumable_safety_stop")
                self.assertEqual(block_event["non_resumable_reason"], "raw_http_resolved_target_scope")
                self.assertIn("resume stopped because this blocked run is a target-scope or DNS safety stop", payload["verification"]["checks"])
                self.assertTrue(payload["verification"]["policy_guard"]["non_resumable_safety_stop"])
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy

    def test_url_capable_provider_blocks_unverified_public_hostname_before_dispatch(self):
        def fake_getaddrinfo(host, port, type=0):
            self.assertEqual(host, "unresolved.example.test")
            raise OSError("simulated DNS failure")

        old_token = os.environ.get("HYPERBROWSER_API_KEY")
        try:
            os.environ["HYPERBROWSER_API_KEY"] = "test-token"
            plan = build_plan(
                infer_task(
                    "Extract titles from this page",
                    url="https://unresolved.example.test/page",
                    providers_allowed=["hyperbrowser"],
                )
            )
            with tempfile.TemporaryDirectory() as tmp:
                with patch("super_browser.adapters.socket.getaddrinfo", side_effect=fake_getaddrinfo):
                    with patch("super_browser.adapters._http_json") as http_mock:
                        result = execute_plan(plan, "run_provider_dns_failure_guard", state_dir=Path(tmp), use_fallbacks=False)
                payload = result.to_dict()
                self.assertEqual(payload["status"], "blocked")
                self.assertFalse(http_mock.called)
                self.assertEqual(payload["provider"], "hyperbrowser")
                self.assertIn("public target DNS resolution failed", payload["verification"]["checks"])
                metadata_artifact = next(item for item in payload["artifacts"] if item["type"] == "metadata")
                with open(metadata_artifact["path"], encoding="utf-8") as handle:
                    metadata = json.load(handle)
                self.assertEqual(metadata["blocked_scope"], "unresolved_public_web")
                self.assertEqual(metadata["target_evidence"]["resolution_error"], "simulated DNS failure")
        finally:
            if old_token is None:
                os.environ.pop("HYPERBROWSER_API_KEY", None)
            else:
                os.environ["HYPERBROWSER_API_KEY"] = old_token

    def test_playwright_blocks_browser_request_to_sensitive_target_scope(self):
        seen = {}

        class FakePlaywrightError(Exception):
            pass

        class FakeRequest:
            url = "http://169.254.169.254/latest/meta-data"
            method = "GET"
            resource_type = "document"

        class FakeRoute:
            request = FakeRequest()

            def abort(self):
                seen["aborted"] = True
                raise FakePlaywrightError("blocked link-local request")

            def continue_(self):
                seen["continued"] = True

        class FakePage:
            def route(self, pattern, handler):
                seen["route_pattern"] = pattern
                self.handler = handler

            def goto(self, url, *args, **kwargs):
                seen["goto"] = url
                self.handler(FakeRoute())

            def title(self):  # pragma: no cover - guard blocks before capture
                raise AssertionError("title should not be read after a blocked browser request")

        class FakeBrowser:
            def new_page(self):
                return FakePage()

            def close(self):
                seen["closed"] = True

        class FakeChromium:
            def launch(self, headless=True):
                seen["headless"] = headless
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        playwright_module = types.ModuleType("playwright")
        sync_api_module = types.ModuleType("playwright.sync_api")
        sync_api_module.Error = FakePlaywrightError
        sync_api_module.sync_playwright = lambda: FakePlaywright()

        with patch.dict(sys.modules, {"playwright": playwright_module, "playwright.sync_api": sync_api_module}):
            plan = build_plan(infer_task("Extract this normal public page", url="https://example.com", providers_allowed=["playwright"]))
            with tempfile.TemporaryDirectory() as tmp:
                result = execute_plan(plan, "run_playwright_browser_scope_guard", state_dir=Path(tmp), use_fallbacks=False)
                payload = result.to_dict()
                self.assertEqual(payload["status"], "blocked")
                self.assertEqual(payload["provider"], "playwright")
                self.assertIn("request to sensitive target scope was blocked", payload["verification"]["checks"])
                self.assertNotIn("screenshot", [artifact["type"] for artifact in payload["artifacts"]])
                self.assertNotIn("text", [artifact["type"] for artifact in payload["artifacts"]])
                metadata_artifact = next(item for item in payload["artifacts"] if item["type"] == "metadata")
                with open(metadata_artifact["path"], encoding="utf-8") as handle:
                    metadata = json.load(handle)
                self.assertTrue(metadata["guard_installed"])
                self.assertEqual(metadata["guard_installed_on"], "page")
                self.assertEqual(metadata["blocked_requests"][0]["target_scope"], "link_local")
                self.assertEqual(metadata["blocked_requests"][0]["url"], "http://169.254.169.254/latest/meta-data")

        self.assertEqual(seen["route_pattern"], "**/*")
        self.assertTrue(seen["aborted"])
        self.assertTrue(seen["closed"])

    def test_playwright_blocks_browser_request_hostname_resolving_to_sensitive_target_scope(self):
        seen = {}

        def fake_getaddrinfo(host, port, type=0):
            self.assertEqual(host, "metadata.example.test")
            return [(None, None, None, None, ("169.254.169.254", port))]

        class FakePlaywrightError(Exception):
            pass

        class FakeRequest:
            url = "http://metadata.example.test/latest/meta-data"
            method = "GET"
            resource_type = "document"

        class FakeRoute:
            request = FakeRequest()

            def abort(self):
                seen["aborted"] = True
                raise FakePlaywrightError("blocked resolved link-local request")

            def continue_(self):
                seen["continued"] = True

        class FakePage:
            def route(self, pattern, handler):
                seen["route_pattern"] = pattern
                self.handler = handler

            def goto(self, url, *args, **kwargs):
                seen["goto"] = url
                self.handler(FakeRoute())

            def title(self):  # pragma: no cover - guard blocks before capture
                raise AssertionError("title should not be read after a blocked browser request")

        class FakeBrowser:
            def new_page(self):
                return FakePage()

            def close(self):
                seen["closed"] = True

        class FakeChromium:
            def launch(self, headless=True):
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        playwright_module = types.ModuleType("playwright")
        sync_api_module = types.ModuleType("playwright.sync_api")
        sync_api_module.Error = FakePlaywrightError
        sync_api_module.sync_playwright = lambda: FakePlaywright()

        with patch.dict(sys.modules, {"playwright": playwright_module, "playwright.sync_api": sync_api_module}):
            with patch("super_browser.adapters.socket.getaddrinfo", side_effect=fake_getaddrinfo):
                plan = build_plan(infer_task("Extract this normal public page", url="https://example.com", providers_allowed=["playwright"]))
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute_plan(plan, "run_playwright_browser_dns_scope_guard", state_dir=Path(tmp), use_fallbacks=False)
                    payload = result.to_dict()
                    self.assertEqual(payload["status"], "blocked")
                    self.assertEqual(payload["provider"], "playwright")
                    metadata_artifact = next(item for item in payload["artifacts"] if item["type"] == "metadata")
                    with open(metadata_artifact["path"], encoding="utf-8") as handle:
                        metadata = json.load(handle)
                    self.assertEqual(metadata["blocked_requests"][0]["target_scope"], "public_web")
                    self.assertEqual(metadata["blocked_requests"][0]["resolved_addresses"][0]["target_scope"], "link_local")
                    self.assertEqual(metadata["blocked_requests"][0]["resolved_addresses"][0]["address"], "169.254.169.254")

        self.assertEqual(seen["route_pattern"], "**/*")
        self.assertTrue(seen["aborted"])
        self.assertTrue(seen["closed"])

    def test_playwright_close_failure_is_warning_after_capture(self):
        seen = {}

        class FakePage:
            def route(self, pattern, handler):
                seen["route_pattern"] = pattern

            def goto(self, url, *args, **kwargs):
                seen["goto"] = url

            def title(self):
                return "Playwright Fixture"

            def locator(self, selector):
                class Locator:
                    def inner_text(self, timeout):
                        return "fixture text"

                return Locator()

            def screenshot(self, path, full_page):
                Path(path).write_bytes(b"fakepng")

        class FakeBrowser:
            def new_page(self):
                return FakePage()

            def close(self):
                seen["close_attempted"] = True
                raise RuntimeError("close failed after capture")

        class FakeChromium:
            def launch(self, headless=True):
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        playwright_module = types.ModuleType("playwright")
        sync_api_module = types.ModuleType("playwright.sync_api")
        sync_api_module.Error = Exception
        sync_api_module.sync_playwright = lambda: FakePlaywright()

        with patch.dict(sys.modules, {"playwright": playwright_module, "playwright.sync_api": sync_api_module}):
            plan = build_plan(infer_task("Extract this normal public page", url="https://example.com", providers_allowed=["playwright"]))
            with tempfile.TemporaryDirectory() as tmp:
                result = execute_plan(plan, "run_playwright_close_warning", state_dir=Path(tmp), use_fallbacks=False)
        payload = result.to_dict()
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["provider"], "playwright")
        self.assertTrue(seen["close_attempted"])
        self.assertIn("browser close failed after capture", payload["verification"]["checks"])
        self.assertIn("browser_close_failed", [event.get("reason") for event in payload["events"]])

    def test_remote_url_providers_preflight_public_hostname_resolution(self):
        provider_envs = {
            "browser-use": {"BROWSER_USE_API_KEY": "browser-use-test-key"},
            "orgo": {"ORGO_API_KEY": "orgo-test-key", "ORGO_COMPUTER_ID": "computer-test"},
            "airtop": {"AIRTOP_API_KEY": "airtop-test-key"},
            "hyperbrowser": {"HYPERBROWSER_API_KEY": "hyperbrowser-test-key"},
            "steel": {"STEEL_API_KEY": "steel-test-key"},
        }

        def fake_getaddrinfo(host, port, type=0):
            self.assertEqual(host, "metadata.example.test")
            return [(None, None, None, None, ("169.254.169.254", port))]

        env_names = sorted({name for env in provider_envs.values() for name in env})
        old_env = {name: os.environ.get(name) for name in env_names}
        try:
            for provider_name, env in provider_envs.items():
                with self.subTest(provider=provider_name):
                    for name in env_names:
                        os.environ.pop(name, None)
                    os.environ.update(env)
                    plan = build_plan(
                        infer_task(
                            "Extract this provider target",
                            url="http://metadata.example.test/provider-target",
                            providers_allowed=[provider_name],
                        )
                    )
                    with tempfile.TemporaryDirectory() as tmp:
                        with patch("super_browser.adapters.socket.getaddrinfo", side_effect=fake_getaddrinfo):
                            with patch("super_browser.adapters._http_json") as http_json_mock:
                                with patch("super_browser.adapters.urlopen") as urlopen_mock:
                                    with patch("super_browser.adapters.shutil.which") as which_mock:
                                        result = execute_plan(plan, f"run_{provider_name}_dns_preflight", state_dir=Path(tmp), use_fallbacks=False)
                        payload = result.to_dict()
                        self.assertEqual(payload["status"], "blocked")
                        self.assertEqual(payload["provider"], provider_name)
                        self.assertFalse(http_json_mock.called)
                        self.assertFalse(urlopen_mock.called)
                        self.assertFalse(which_mock.called)
                        self.assertIn("resolved provider target to sensitive scope was blocked", payload["verification"]["checks"])
                        self.assertIn("provider_url_resolved_target_scope", [event["reason"] for event in payload["events"] if event["type"] == "blocked"])
                        metadata_artifact = next(item for item in payload["artifacts"] if item["type"] == "metadata")
                        with open(metadata_artifact["path"], encoding="utf-8") as handle:
                            metadata = json.load(handle)
                        self.assertEqual(metadata["target_scope"], "public_web")
                        self.assertEqual(metadata["blocked_scope"], "link_local")
                        self.assertEqual(metadata["target_evidence"]["resolved_addresses"][0]["target_scope"], "link_local")
        finally:
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_plan_only_does_not_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Fetch this JSON endpoint through raw HTTP", url="http://127.0.0.1:1/nope", execute=False)
            payload = run.to_dict()
            self.assertEqual(payload["status"], "planned")
            self.assertEqual([artifact["type"] for artifact in payload["artifacts"]], ["plan"])

    def test_create_run_rejects_unknown_provider_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            with self.assertRaisesRegex(ValueError, "Unknown provider in providers_allowed: typo-provider"):
                create_run(
                    "Extract titles from https://example.com",
                    execute=False,
                    providers_allowed=["typo-provider"],
                )
            self.assertFalse(os.path.exists(os.path.join(tmp, "runs.sqlite")))

    def test_create_run_rejects_invalid_optimize_before_state_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            with self.assertRaisesRegex(ValueError, "Invalid optimize value: cheapest"):
                create_run("Extract titles from https://example.com", execute=False, optimize="cheapest")
            self.assertFalse(os.path.exists(os.path.join(tmp, "runs.sqlite")))

    def test_create_run_rejects_negative_max_cost_before_state_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            with self.assertRaisesRegex(ValueError, "max_cost_usd must be >= 0"):
                create_run("Extract titles from https://example.com", execute=False, max_cost_usd=-1)
            self.assertFalse(os.path.exists(os.path.join(tmp, "runs.sqlite")))

    def test_create_run_rejects_url_credentials_before_state_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            with self.assertRaisesRegex(ValueError, "url must not contain username or password"):
                create_run("Extract titles from https://agent:needle-secret@example.com/private", execute=False)
            self.assertFalse(os.path.exists(os.path.join(tmp, "runs.sqlite")))

    def test_resume_planned_run_executes_and_records_report(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            url = f"http://127.0.0.1:{server.server_port}/data.json"
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                run = create_run("Fetch this JSON endpoint through raw HTTP", url=url, execute=False)
                resumed = resume_run(run.run_id)
                payload = resumed.to_dict()
                self.assertEqual(payload["status"], "complete")
                self.assertIn("execution_resumed", [event["type"] for event in payload["events"]])
                self.assertEqual(payload["verification"]["selected_provider"], "decodo-http")
                self.assertEqual(len([item for item in payload["artifacts"] if item["type"] == "run_report"]), 1)
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            server.shutdown()
            server.server_close()

    def test_resume_failed_run_replaces_stale_execution_artifacts_on_success(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            url = f"http://127.0.0.1:{server.server_port}/data.json"
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                run = create_run("Fetch this JSON endpoint through raw HTTP", url=url, providers_allowed=["decodo-http"], execute=False)
                report_path = _artifact_path(tmp, run.run_id, "run-report.json")
                with open(report_path, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "run_id": run.run_id,
                            "plan_sha256": plan_fingerprint(run.plan),
                            "final_provider": "decodo-http",
                            "final_status": "failed",
                            "attempts": [{"order": 1, "provider": "decodo-http", "status": "failed", "error": "old failure"}],
                        },
                        handle,
                    )
                old_sha256 = hashlib.sha256(Path(report_path).read_bytes()).hexdigest()
                run.status = "failed"
                run.artifacts.append({"type": "run_report", "provider": "decodo-http", "path": report_path, "sha256": old_sha256})
                run.verification = {"confidence": "medium", "selected_provider": "decodo-http", "checks": ["old failed run"]}
                RunStore().save(run)

                resumed = resume_run(run.run_id)
                verified = verify_run(run.run_id)

                payload = resumed.to_dict()
                self.assertEqual(payload["status"], "complete")
                self.assertEqual([item["type"] for item in payload["artifacts"] if item["type"] == "run_report"], ["run_report"])
                self.assertEqual(verified["confidence"], "high")
                self.assertEqual(verified["failures"], [])
                self.assertEqual(verified["run_report"]["final_status"], "complete")
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            server.shutdown()
            server.server_close()

    def test_resume_blocks_tampered_provider_outside_task_allowlist_before_execution_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", providers_allowed=["playwright"], execute=False)
            run.plan["primary_provider"] = "decodo-http"
            run.plan["fallback_providers"] = []
            RunStore().save(run)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "planned")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "provider_constraints")
            self.assertEqual(block_event["evidence_integrity_status"], "provider_allowlist_violation")
            self.assertIn("resume stopped because provider sequence violates task constraints", payload["verification"]["checks"])
            self.assertEqual(payload["verification"]["failures"][0]["type"], "provider_allowlist_violation")

    def test_resume_blocks_invalid_task_constraints_before_execution_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            run.plan["task"]["max_cost_usd"] = math.nan
            RunStore().save(run)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "planned")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "provider_constraints")
            self.assertEqual(block_event["evidence_integrity_status"], "provider_constraint_invalid_task")
            self.assertIn("resume stopped because provider sequence violates task constraints", payload["verification"]["checks"])
            self.assertEqual(payload["verification"]["failures"][0]["type"], "provider_constraint_invalid_task")

    def test_resume_failed_run_with_mismatched_run_report_does_not_execute_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            report_path = _artifact_path(tmp, run.run_id, "run-report.json")
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "run_id": run.run_id,
                        "plan_sha256": "0" * 64,
                        "final_provider": "playwright",
                        "final_status": "failed",
                        "attempts": [{"order": 1, "provider": "playwright", "status": "failed", "error": "simulated failure"}],
                    },
                    handle,
                )
            run.status = "failed"
            run.artifacts.append({"type": "run_report", "provider": "playwright", "path": report_path})
            run.verification = {"confidence": "medium", "selected_provider": "playwright", "checks": ["simulated failed run"]}
            RunStore().save(run)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "failed")
            self.assertFalse(execute_mock.called)
            self.assertIn("resume_blocked", [event["type"] for event in payload["events"]])
            block_event = payload["events"][-1]
            self.assertEqual(block_event["reason"], "run_report_plan_integrity")
            self.assertEqual(block_event["plan_integrity_status"], "mismatch")
            self.assertIn("resume stopped because run-report evidence no longer matches the current plan", payload["verification"]["checks"])
            self.assertEqual(payload["verification"]["plan_integrity"]["status"], "mismatch")
            stored = RunStore().get(run.run_id)
            self.assertEqual(stored["status"], "failed")
            self.assertIn("run_report_plan_integrity", [event.get("reason") for event in stored["events"]])

    def test_resume_failed_run_with_run_report_status_mismatch_does_not_execute_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            report_path = _artifact_path(tmp, run.run_id, "run-report.json")
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "run_id": run.run_id,
                        "plan_sha256": plan_fingerprint(run.plan),
                        "final_provider": "playwright",
                        "final_status": "complete",
                        "attempts": [{"order": 1, "provider": "playwright", "status": "complete"}],
                    },
                    handle,
                )
            run.status = "failed"
            run.artifacts.append({"type": "run_report", "provider": "playwright", "path": report_path})
            run.verification = {"confidence": "medium", "selected_provider": "playwright", "checks": ["simulated inconsistent run"]}
            RunStore().save(run)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "failed")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "run_report_status_integrity")
            self.assertEqual(block_event["run_report_integrity_status"], "status_mismatch")
            self.assertEqual(block_event["run_status"], "failed")
            self.assertEqual(block_event["run_report_final_status"], "complete")
            self.assertIn("resume stopped because run-report final_status does not match the current run status", payload["verification"]["checks"])
            self.assertEqual(payload["verification"]["run_report_integrity"]["status"], "mismatch")
            stored = RunStore().get(run.run_id)
            self.assertIn("run_report_status_integrity", [event.get("reason") for event in stored["events"]])

    def test_resume_failed_run_with_untrusted_run_report_path_does_not_execute_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = str(Path(tmp) / "state")
            run = create_run("Extract titles from https://example.com", execute=False)
            report_path = os.path.join(tmp, "outside-run-report.json")
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "run_id": run.run_id,
                        "plan_sha256": plan_fingerprint(run.plan),
                        "final_provider": "playwright",
                        "final_status": "failed",
                        "attempts": [{"order": 1, "provider": "playwright", "status": "failed", "error": "simulated failure"}],
                        "secret": "needle-runtime-secret",
                    },
                    handle,
                )
            run.status = "failed"
            run.artifacts.append({"type": "run_report", "provider": "playwright", "path": report_path})
            run.verification = {"confidence": "medium", "selected_provider": "playwright", "checks": ["simulated failed run"]}
            RunStore().save(run)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "failed")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "run_report_evidence_integrity")
            self.assertEqual(block_event["evidence_integrity_status"], "untrusted_artifact_path")
            self.assertEqual(payload["verification"]["run_report_integrity"]["failure_type"], "untrusted_artifact_path")
            self.assertIn("resume stopped because run-report or artifact evidence is missing or inconsistent", payload["verification"]["checks"])
            self.assertNotIn("needle-runtime-secret", json.dumps(payload))

    def test_resume_failed_run_with_run_report_run_id_mismatch_does_not_execute_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            report_path = _artifact_path(tmp, run.run_id, "run-report.json")
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "run_id": "run_from_another_record",
                        "plan_sha256": plan_fingerprint(run.plan),
                        "final_provider": "playwright",
                        "final_status": "failed",
                        "attempts": [{"order": 1, "provider": "playwright", "status": "failed", "error": "simulated failure"}],
                    },
                    handle,
                )
            run.status = "failed"
            run.artifacts.append({"type": "run_report", "provider": "playwright", "path": report_path})
            run.verification = {"confidence": "medium", "selected_provider": "playwright", "checks": ["simulated failed run"]}
            RunStore().save(run)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "failed")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "run_report_evidence_integrity")
            self.assertEqual(block_event["evidence_integrity_status"], "run_report_run_id_mismatch")
            self.assertEqual(payload["verification"]["run_report_integrity"]["failure_type"], "run_report_run_id_mismatch")
            self.assertIn("resume stopped because run-report or artifact evidence is missing or inconsistent", payload["verification"]["checks"])

    def test_resume_run_with_invalid_run_id_payload_does_not_execute_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            run.run_id = ".."
            run.status = "planned"
            run.verification = {"confidence": "medium", "selected_provider": "playwright", "checks": ["simulated corrupted run id"]}
            RunStore().save(run)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run("..")

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "planned")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "run_report_evidence_integrity")
            self.assertEqual(block_event["evidence_integrity_status"], "invalid_run_id")
            self.assertEqual(payload["verification"]["run_report_integrity"]["failure_type"], "invalid_run_id")
            self.assertIn("resume stopped because run-report or artifact evidence is missing or inconsistent", payload["verification"]["checks"])

    def test_resume_failed_run_with_unplanned_final_provider_does_not_execute_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            report_path = _artifact_path(tmp, run.run_id, "run-report.json")
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "run_id": run.run_id,
                        "plan_sha256": plan_fingerprint(run.plan),
                        "final_provider": "browserbase",
                        "final_status": "failed",
                        "attempts": [{"order": 1, "provider": "browserbase", "status": "failed", "error": "simulated failure"}],
                    },
                    handle,
                )
            run.status = "failed"
            run.artifacts.append({"type": "run_report", "provider": "browserbase", "path": report_path})
            run.verification = {"confidence": "medium", "selected_provider": "browserbase", "checks": ["simulated inconsistent run"]}
            RunStore().save(run)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "failed")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "run_report_evidence_integrity")
            self.assertEqual(block_event["evidence_integrity_status"], "run_report_final_provider_not_planned")
            self.assertIn("resume stopped because run-report or artifact evidence is missing or inconsistent", payload["verification"]["checks"])
            self.assertEqual(payload["verification"]["run_report_integrity"]["status"], "untrusted")
            self.assertEqual(payload["verification"]["failures"][0]["type"], "run_report_final_provider_not_planned")

    def test_resume_failed_run_with_missing_final_provider_attempt_does_not_execute_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            report_path = _artifact_path(tmp, run.run_id, "run-report.json")
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "run_id": run.run_id,
                        "plan_sha256": plan_fingerprint(run.plan),
                        "final_provider": "playwright",
                        "final_status": "failed",
                        "attempts": [{"order": 1, "provider": "decodo-http", "status": "failed", "error": "simulated failure"}],
                    },
                    handle,
                )
            run.status = "failed"
            run.artifacts.append({"type": "run_report", "provider": "playwright", "path": report_path})
            run.verification = {"confidence": "medium", "selected_provider": "playwright", "checks": ["simulated inconsistent run"]}
            RunStore().save(run)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "failed")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "run_report_evidence_integrity")
            self.assertEqual(block_event["evidence_integrity_status"], "run_report_final_provider_attempt_missing")
            self.assertIn("resume stopped because run-report or artifact evidence is missing or inconsistent", payload["verification"]["checks"])
            self.assertEqual(payload["verification"]["run_report_integrity"]["failure_type"], "run_report_final_provider_attempt_missing")

    def test_resume_blocked_run_with_missing_run_report_fingerprint_does_not_execute_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            report_path = _artifact_path(tmp, run.run_id, "run-report.json")
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "run_id": run.run_id,
                        "final_provider": "playwright",
                        "final_status": "blocked",
                        "attempts": [{"order": 1, "provider": "playwright", "status": "blocked", "error": "simulated block"}],
                    },
                    handle,
                )
            run.status = "blocked"
            run.artifacts.append({"type": "run_report", "provider": "playwright", "path": report_path})
            run.verification = {"confidence": "medium", "selected_provider": "playwright", "checks": ["simulated blocked run"]}
            RunStore().save(run)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "blocked")
            self.assertFalse(execute_mock.called)
            self.assertIn("resume_blocked", [event["type"] for event in payload["events"]])
            block_event = payload["events"][-1]
            self.assertEqual(block_event["reason"], "run_report_plan_integrity")
            self.assertEqual(block_event["plan_integrity_status"], "missing")
            self.assertIn("resume stopped because run-report evidence no longer matches the current plan", payload["verification"]["checks"])
            self.assertEqual(payload["verification"]["plan_integrity"]["status"], "missing")

    def test_resume_failed_run_without_run_report_does_not_execute_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            run.status = "failed"
            run.verification = {"confidence": "medium", "selected_provider": "playwright", "checks": ["simulated failed run without report"]}
            RunStore().save(run)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "failed")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "run_report_evidence_integrity")
            self.assertEqual(block_event["evidence_integrity_status"], "missing_run_report")
            self.assertIn("resume stopped because run-report or artifact evidence is missing or inconsistent", payload["verification"]["checks"])
            self.assertEqual(payload["verification"]["run_report_integrity"]["failure_type"], "missing_run_report")

    def test_stale_resume_does_not_double_execute_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            store = RunStore()
            run = create_run("Fetch this JSON endpoint through raw HTTP", url="http://127.0.0.1:1/data.json", execute=False)
            first_worker = _run_from_payload(store.get(run.run_id))
            stale_second_worker = _run_from_payload(store.get(run.run_id))
            second_worker_statuses = []

            def fake_execute(plan, run_id, state_dir, approval_context=None, approval_granted=False):
                second_worker = _execute_run(stale_second_worker, store, "execution_resumed")
                second_worker_statuses.append(second_worker.status)
                return ExecutionResult(provider=plan.primary_provider, status="complete", verification={"confidence": "high", "checks": ["fake provider returned"]})

            with patch("super_browser.runtime.execute_plan", side_effect=fake_execute) as execute_mock:
                completed = _execute_run(first_worker, store, "execution_started")

            stored = store.get(run.run_id)
            self.assertEqual(completed.status, "complete")
            self.assertEqual(stored["status"], "complete")
            self.assertEqual(second_worker_statuses, ["executing"])
            self.assertEqual(execute_mock.call_count, 1)
            self.assertEqual(len([event for event in stored["events"] if event["type"] == "execution_started"]), 1)
            self.assertEqual(len([event for event in stored["events"] if event["type"] == "execution_resumed"]), 0)

    def test_resume_active_executing_run_does_not_execute_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            store = RunStore()
            run = create_run("Fetch this JSON endpoint through raw HTTP", url="http://127.0.0.1:1/data.json", execute=False)
            store.claim_execution(
                run.run_id,
                "planned",
                [{"at": utc_now(), "type": "execution_started", "provider": "decodo-http"}],
                lease_seconds=3600,
            )

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            self.assertEqual(resumed.status, "executing")
            self.assertFalse(execute_mock.called)
            self.assertIn("execution_lease_active", [event.get("reason") for event in resumed.events])
            stored = store.get(run.run_id)
            self.assertEqual(stored["status"], "executing")
            self.assertIn("execution_lease", stored)
            self.assertEqual(stored["execution_lease"]["lease_seconds"], 3600)
            self.assertIn("execution_lease_active", [event.get("reason") for event in stored["events"]])

    def test_long_running_lease_uses_policy_when_stored_flag_is_false(self):
        plan = build_plan(infer_task("Monitor this public page overnight and report changes"))
        plan.task.long_running = False

        self.assertEqual(_execution_lease_seconds(plan), LONG_RUNNING_EXECUTION_LEASE_SECONDS)

    def test_normal_lease_does_not_inherit_stale_long_running_flag(self):
        plan = build_plan(infer_task("Extract titles from https://example.com"))

        self.assertEqual(_execution_lease_seconds(plan), DEFAULT_EXECUTION_LEASE_SECONDS)

    def test_completed_run_clears_execution_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            store = RunStore()
            run = create_run("Fetch this JSON endpoint through raw HTTP", url="http://127.0.0.1:1/data.json", execute=False)
            result = ExecutionResult(provider="decodo-http", status="complete", verification={"confidence": "high", "checks": ["fake provider returned"]})

            with patch("super_browser.runtime.execute_plan", return_value=result):
                completed = resume_run(run.run_id)

            stored = store.get(run.run_id)
            self.assertEqual(completed.status, "complete")
            self.assertEqual(stored["status"], "complete")
            self.assertEqual(stored["execution_lease"], {})

    def test_runtime_execution_exception_is_saved_as_failed_run_and_clears_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            store = RunStore()
            run = create_run("Extract titles from https://example.com", execute=False)

            with patch("super_browser.runtime.execute_plan", side_effect=RuntimeError("BROWSER_USE_API_KEY=runtime-secret")):
                failed = resume_run(run.run_id)

            payload = failed.to_dict()
            stored = store.get(run.run_id)
            report = verify_run(run.run_id)
            serialized = json.dumps({"payload": payload, "report": report})

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(stored["status"], "failed")
            self.assertEqual(stored["execution_lease"], {})
            self.assertIn("execution_exception", [event["type"] for event in payload["events"]])
            self.assertIn("execution_error", [event["type"] for event in payload["events"]])
            self.assertIn("runtime_exception", [artifact["type"] for artifact in payload["artifacts"]])
            self.assertIn("run_report", [artifact["type"] for artifact in payload["artifacts"]])
            self.assertEqual(report["run_report"]["final_status"], "failed")
            self.assertEqual(report["run_report"]["final_provider"], payload["plan"]["primary_provider"])
            self.assertEqual(report["failures"], [])
            self.assertIn("runtime execution exception was captured", report["checks"])
            self.assertNotIn("runtime-secret", serialized)
            self.assertIn("BROWSER_USE_API_KEY=[REDACTED]", serialized)

    def test_resume_expired_executing_run_recovers_and_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            store = RunStore()
            run = create_run("Fetch this JSON endpoint through raw HTTP", url="http://127.0.0.1:1/data.json", execute=False)
            store.claim_execution(
                run.run_id,
                "planned",
                [{"at": utc_now(), "type": "execution_started", "provider": "decodo-http"}],
                lease_seconds=0,
            )
            complete_result = ExecutionResult(provider="decodo-http", status="complete", verification={"confidence": "high", "checks": ["recovered execution"]})

            with patch("super_browser.runtime.execute_plan", return_value=complete_result) as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(execute_mock.call_count, 1)
            self.assertIn("stale_execution_recovered", [event["type"] for event in payload["events"]])
            self.assertIn("execution_resumed_after_stale", [event["type"] for event in payload["events"]])

    def test_external_provider_missing_env_contract(self):
        old_key = os.environ.pop("BROWSER_USE_API_KEY", None)
        try:
            plan = build_plan(infer_task("Search Facebook Ads Library behind Cloudflare and extract advertisers"))
            with tempfile.TemporaryDirectory() as tmp:
                result = execute_plan(plan, "run_contract", state_dir=Path(tmp), use_fallbacks=False)
            payload = result.to_dict()
            self.assertEqual(payload["status"], "blocked")
            self.assertEqual(payload["provider"], "browser-use")
            self.assertIn("BROWSER_USE_API_KEY", payload["error"])
            self.assertEqual(payload["artifacts"][0]["type"], "provider_docs")
        finally:
            if old_key is not None:
                os.environ["BROWSER_USE_API_KEY"] = old_key

    def test_execute_plan_blocks_approval_gated_plan_without_approval_context(self):
        plan = build_plan(infer_task("Post this comment on LinkedIn", url="https://example.com"))
        with tempfile.TemporaryDirectory() as tmp:
            with patch("super_browser.adapters.get_adapter") as adapter_mock:
                result = execute_plan(plan, "run_direct_approval_guard", state_dir=Path(tmp), use_fallbacks=False)
        payload = result.to_dict()
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(adapter_mock.called)
        self.assertIn("durable approval context", payload["error"])
        self.assertIn("approval-gated plan was not executed", payload["verification"]["checks"])

    def test_execute_plan_rechecks_policy_when_plan_flag_is_wrong(self):
        plan = build_plan(infer_task("Post this comment on LinkedIn", url="https://example.com"))
        plan.approval_required = False
        with tempfile.TemporaryDirectory() as tmp:
            with patch("super_browser.adapters.get_adapter") as adapter_mock:
                result = execute_plan(plan, "run_mutated_approval_guard", state_dir=Path(tmp), use_fallbacks=False)
        payload = result.to_dict()
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(adapter_mock.called)
        self.assertIn("approval-gated plan was not executed", payload["verification"]["checks"])

    def test_execute_plan_blocks_provider_outside_task_allowlist(self):
        plan = build_plan(infer_task("Extract titles from https://example.com", providers_allowed=["playwright"]))
        plan.primary_provider = "decodo-http"
        plan.fallback_providers = []

        with tempfile.TemporaryDirectory() as tmp:
            with patch("super_browser.adapters.get_adapter") as adapter_mock:
                result = execute_plan(plan, "run_provider_allowlist_guard", state_dir=Path(tmp), use_fallbacks=False)

            payload = result.to_dict()
            self.assertEqual(payload["status"], "blocked")
            self.assertFalse(adapter_mock.called)
            self.assertIn("Provider execution violates task constraints", payload["error"])
            self.assertIn("provider sequence constraints were enforced before execution", payload["verification"]["checks"])
            self.assertEqual(payload["verification"]["failures"][0]["type"], "provider_allowlist_violation")
            report_artifact = next(item for item in payload["artifacts"] if item["type"] == "run_report")
            with open(report_artifact["path"], encoding="utf-8") as handle:
                report = json.load(handle)
            self.assertEqual(report["final_status"], "blocked")
            self.assertEqual(report["attempts"], [])

    def test_execute_plan_blocks_provider_over_task_max_cost(self):
        plan = build_plan(infer_task("Extract titles from https://example.com", max_cost_usd=0))
        plan.primary_provider = "decodo-http"
        plan.fallback_providers = []

        with tempfile.TemporaryDirectory() as tmp:
            with patch("super_browser.adapters.get_adapter") as adapter_mock:
                result = execute_plan(plan, "run_provider_cost_guard", state_dir=Path(tmp), use_fallbacks=False)

        payload = result.to_dict()
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(adapter_mock.called)
        self.assertIn("Provider execution violates task constraints", payload["error"])
        self.assertEqual(payload["verification"]["failures"][0]["type"], "provider_cost_constraint_violation")
        self.assertEqual(payload["verification"]["failures"][0]["max_cost_usd"], 0.0)

    def test_execute_plan_blocks_stale_target_scope_before_adapter_dispatch(self):
        plan = build_plan(
            infer_task(
                "Fetch this JSON endpoint through raw HTTP",
                url="http://169.254.169.254/latest/meta-data",
                providers_allowed=["decodo-http"],
            )
        )
        plan.approval_required = False
        plan.task.target_scope = "public_web"

        with tempfile.TemporaryDirectory() as tmp:
            with patch("super_browser.adapters.get_adapter") as adapter_mock:
                result = execute_plan(plan, "run_target_scope_mismatch_guard", state_dir=Path(tmp), use_fallbacks=False)

        payload = result.to_dict()
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(adapter_mock.called)
        self.assertIn("Provider execution violates task constraints", payload["error"])
        self.assertEqual(payload["verification"]["failures"][0]["type"], "provider_target_scope_mismatch")
        self.assertEqual(payload["verification"]["failures"][0]["declared_target_scope"], "public_web")
        self.assertEqual(payload["verification"]["failures"][0]["derived_target_scope"], "link_local")

    def test_execute_plan_blocks_url_required_primary_without_url_before_adapter_dispatch(self):
        plan = build_plan(infer_task("Search the web for public mentions of this brand"))
        plan.primary_provider = "steel"
        plan.fallback_providers = []

        with tempfile.TemporaryDirectory() as tmp:
            with patch("super_browser.adapters.get_adapter") as adapter_mock:
                result = execute_plan(plan, "run_missing_url_provider_guard", state_dir=Path(tmp), use_fallbacks=False)

        payload = result.to_dict()
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(adapter_mock.called)
        self.assertIn("Provider execution violates task constraints", payload["error"])
        self.assertEqual(payload["verification"]["failures"][0]["type"], "provider_missing_url_constraint_violation")
        self.assertEqual(payload["verification"]["failures"][0]["provider"], "steel")

    def test_execute_plan_blocks_raw_http_without_http_url_before_adapter_dispatch(self):
        plan = build_plan(infer_task("Search the web for public mentions of this brand"))
        plan.task.raw_http = True

        with tempfile.TemporaryDirectory() as tmp:
            with patch("super_browser.adapters.get_adapter") as adapter_mock:
                result = execute_plan(plan, "run_missing_raw_http_url_guard", state_dir=Path(tmp), use_fallbacks=False)

        payload = result.to_dict()
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(adapter_mock.called)
        self.assertIn("Provider execution violates task constraints", payload["error"])
        self.assertEqual(payload["verification"]["failures"][0]["type"], "provider_raw_http_url_constraint_violation")
        self.assertEqual(payload["verification"]["failures"][0]["allowed_schemes"], ["http", "https"])

    def test_execute_plan_blocks_invalid_task_constraints_before_adapter_dispatch(self):
        plan = build_plan(infer_task("Extract titles from https://example.com"))
        plan.task.max_cost_usd = math.nan
        plan.primary_provider = "decodo-http"
        plan.fallback_providers = []

        with tempfile.TemporaryDirectory() as tmp:
            with patch("super_browser.adapters.get_adapter") as adapter_mock:
                result = execute_plan(plan, "run_invalid_task_constraint_guard", state_dir=Path(tmp), use_fallbacks=False)

        payload = result.to_dict()
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(adapter_mock.called)
        self.assertIn("Provider execution violates task constraints", payload["error"])
        self.assertEqual(payload["verification"]["failures"][0]["type"], "provider_constraint_invalid_task")

    def test_execute_plan_blocks_approval_gated_plan_with_bare_approval_boolean(self):
        plan = build_plan(infer_task("Post this comment on LinkedIn", url="https://example.com"))
        with tempfile.TemporaryDirectory() as tmp:
            with patch("super_browser.adapters.get_adapter") as adapter_mock:
                result = execute_plan(plan, "run_direct_approval_bool_guard", state_dir=Path(tmp), use_fallbacks=False, approval_granted=True)
        payload = result.to_dict()
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(adapter_mock.called)
        self.assertIn("bare approval_granted=True is not sufficient", payload["error"])

    def test_execute_plan_blocks_approval_gated_plan_with_wrong_approval_context(self):
        plan = build_plan(infer_task("Post this comment on LinkedIn", url="https://example.com"))
        context = _approval_context_for(plan)
        context["action_fingerprint"] = "wrong"
        with tempfile.TemporaryDirectory() as tmp:
            with patch("super_browser.adapters.get_adapter") as adapter_mock:
                result = execute_plan(plan, "run_direct_approval_context_guard", state_dir=Path(tmp), use_fallbacks=False, approval_context=context)
        payload = result.to_dict()
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(adapter_mock.called)
        self.assertIn("approval_context action fingerprint does not match plan", payload["error"])

    def test_execute_plan_allows_approval_gated_plan_with_explicit_approval_context(self):
        plan = build_plan(infer_task("Post this comment on LinkedIn", url="https://example.com"))

        class FakeAdapter:
            def execute(self, provider_plan, run_id, artifact_dir):
                return ExecutionResult(provider=provider_plan.primary_provider, status="complete", verification={"confidence": "high", "checks": ["fake approved execution"]})

        with tempfile.TemporaryDirectory() as tmp:
            with patch("super_browser.adapters.get_adapter", return_value=FakeAdapter()) as adapter_mock:
                result = execute_plan(plan, "run_direct_approval_allowed", state_dir=Path(tmp), use_fallbacks=False, approval_context=_approval_context_for(plan))
        payload = result.to_dict()
        self.assertEqual(payload["status"], "complete")
        self.assertTrue(adapter_mock.called)
        self.assertIn("fake approved execution", payload["verification"]["checks"])

    def test_raw_http_rejects_file_url_even_when_plan_is_hand_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret_path = Path(tmp) / "local-secret.txt"
            secret_path.write_text("needle-local-file-secret", encoding="utf-8")
            plan = build_plan(infer_task("Extract a local browser fixture", url=secret_path.as_uri()))
            plan.primary_provider = "decodo-http"
            plan.fallback_providers = []

            with patch("super_browser.adapters.get_adapter") as adapter_mock:
                result = execute_plan(plan, "run_raw_http_file_guard", state_dir=Path(tmp), use_fallbacks=False, approval_context=_approval_context_for(plan))
            payload = result.to_dict()

            self.assertEqual(payload["status"], "blocked")
            self.assertFalse(adapter_mock.called)
            self.assertEqual(payload["provider"], "decodo-http")
            self.assertIn("Provider execution violates task constraints", payload["error"])
            self.assertEqual(payload["verification"]["failures"][0]["type"], "provider_file_url_constraint_violation")
            self.assertIn("provider sequence constraints were enforced before execution", payload["verification"]["checks"])
            self.assertNotIn("http_response", [artifact["type"] for artifact in payload["artifacts"]])
            self.assertNotIn("needle-local-file-secret", json.dumps(payload))

    def test_fallback_execution_uses_next_provider_and_writes_report(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old_browser_use_key = os.environ.pop("BROWSER_USE_API_KEY", None)
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            url = f"http://127.0.0.1:{server.server_port}/data.json"
            plan = build_plan(infer_task("Extract this protected page with fallback", url=url))
            plan.primary_provider = "browser-use"
            plan.fallback_providers = ["decodo-http"]
            with tempfile.TemporaryDirectory() as tmp:
                result = execute_plan(plan, "run_fallback", state_dir=Path(tmp))
                payload = result.to_dict()
                report_artifacts = [item for item in payload["artifacts"] if item["type"] == "run_report"]
                self.assertEqual(len(report_artifacts), 1)
                with open(report_artifacts[0]["path"], encoding="utf-8") as handle:
                    report = json.load(handle)
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(payload["provider"], "decodo-http")
            self.assertEqual(payload["verification"]["selected_provider"], "decodo-http")
            self.assertEqual([attempt["provider"] for attempt in payload["verification"]["attempts"]], ["browser-use", "decodo-http"])
            self.assertEqual(payload["verification"]["attempts"][0]["status"], "blocked")
            self.assertEqual(payload["verification"]["attempts"][1]["status"], "complete")
            self.assertEqual(report["final_provider"], "decodo-http")
            self.assertEqual(len(report["attempts"]), 2)
        finally:
            if old_browser_use_key is not None:
                os.environ["BROWSER_USE_API_KEY"] = old_browser_use_key
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            server.shutdown()
            server.server_close()

    def test_provider_adapter_exception_is_captured_and_fallback_runs(self):
        class RaisingAdapter:
            def execute(self, provider_plan, run_id, artifact_dir):
                raise RuntimeError("BROWSER_USE_API_KEY=super-secret-provider-key")

        class CompleteAdapter:
            def execute(self, provider_plan, run_id, artifact_dir):
                output_path = artifact_dir / "fallback-output.json"
                output_path.write_text('{"ok": true}', encoding="utf-8")
                return ExecutionResult(
                    provider=provider_plan.primary_provider,
                    status="complete",
                    artifacts=[{"type": "metadata", "path": str(output_path), "provider": provider_plan.primary_provider}],
                    verification={"confidence": "high", "checks": ["fallback adapter ran"]},
                )

        adapters = {
            "browser-use": RaisingAdapter(),
            "decodo-http": CompleteAdapter(),
        }

        def fake_get_adapter(provider_name):
            return adapters[provider_name]

        plan = build_plan(infer_task("Extract titles from https://example.com"))
        plan.primary_provider = "browser-use"
        plan.fallback_providers = ["decodo-http"]

        with tempfile.TemporaryDirectory() as tmp:
            with patch("super_browser.adapters.get_adapter", side_effect=fake_get_adapter):
                result = execute_plan(plan, "run_provider_exception_fallback", state_dir=Path(tmp))
            payload = result.to_dict()
            report_artifact = next(item for item in payload["artifacts"] if item["type"] == "run_report")
            exception_artifact = next(item for item in payload["artifacts"] if item.get("reason") == "provider_exception")
            with open(report_artifact["path"], encoding="utf-8") as handle:
                report = json.load(handle)
            with open(exception_artifact["path"], encoding="utf-8") as handle:
                exception_payload = json.load(handle)

        serialized = json.dumps(payload)
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["provider"], "decodo-http")
        self.assertEqual(payload["verification"]["selected_provider"], "decodo-http")
        self.assertEqual([attempt["provider"] for attempt in payload["verification"]["attempts"]], ["browser-use", "decodo-http"])
        self.assertEqual(payload["verification"]["attempts"][0]["status"], "failed")
        self.assertEqual(payload["verification"]["attempts"][1]["status"], "complete")
        self.assertIn("browser-use adapter raised RuntimeError", payload["verification"]["attempts"][0]["error"])
        self.assertIn("BROWSER_USE_API_KEY=[REDACTED]", payload["verification"]["attempts"][0]["error"])
        self.assertNotIn("super-secret-provider-key", serialized)
        self.assertEqual(exception_payload["provider"], "browser-use")
        self.assertEqual(exception_payload["error_type"], "RuntimeError")
        self.assertIn("[REDACTED]", exception_payload["error"])
        self.assertEqual(report["final_provider"], "decodo-http")
        self.assertEqual(report["attempts"][0]["status"], "failed")
        self.assertEqual(report["attempts"][1]["status"], "complete")

    def test_provider_adapter_exceptions_still_write_failed_run_report(self):
        class RaisingAdapter:
            def __init__(self, provider_name):
                self.provider_name = provider_name

            def execute(self, provider_plan, run_id, artifact_dir):
                raise ValueError(f"{self.provider_name.upper().replace('-', '_')}_TOKEN=provider-secret")

        def fake_get_adapter(provider_name):
            return RaisingAdapter(provider_name)

        plan = build_plan(infer_task("Extract titles from https://example.com"))
        plan.primary_provider = "browser-use"
        plan.fallback_providers = ["decodo-http"]

        with tempfile.TemporaryDirectory() as tmp:
            with patch("super_browser.adapters.get_adapter", side_effect=fake_get_adapter):
                result = execute_plan(plan, "run_provider_exception_failed", state_dir=Path(tmp))
            payload = result.to_dict()
            report_artifact = next(item for item in payload["artifacts"] if item["type"] == "run_report")
            exception_artifacts = [item for item in payload["artifacts"] if item.get("reason") == "provider_exception"]
            with open(report_artifact["path"], encoding="utf-8") as handle:
                report = json.load(handle)

        serialized = json.dumps(payload)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["provider"], "decodo-http")
        self.assertEqual(len(exception_artifacts), 2)
        self.assertEqual([attempt["status"] for attempt in payload["verification"]["attempts"]], ["failed", "failed"])
        self.assertIn("All provider attempts stopped", payload["error"])
        self.assertIn("provider adapter exception was captured", payload["verification"]["checks"])
        self.assertNotIn("provider-secret", serialized)
        self.assertEqual(report["final_status"], "failed")
        self.assertEqual(report["final_provider"], "decodo-http")
        self.assertEqual(len(report["attempts"]), 2)

    def test_browser_use_adapter_with_fake_sdk(self):
        old_key = os.environ.get("BROWSER_USE_API_KEY")
        os.environ["BROWSER_USE_API_KEY"] = "test-key"
        package = types.ModuleType("browser_use_sdk")
        v3 = types.ModuleType("browser_use_sdk.v3")

        class FakeResult:
            output = {"ok": True}
            status = "done"
            liveUrl = "https://browser-use.test/live"

        class FakeAsyncBrowserUse:
            async def run(self, prompt):
                self.prompt = prompt
                return FakeResult()

        v3.AsyncBrowserUse = FakeAsyncBrowserUse
        try:
            with patch.dict(sys.modules, {"browser_use_sdk": package, "browser_use_sdk.v3": v3}):
                plan = build_plan(infer_task("Search Facebook Ads Library behind Cloudflare and extract advertisers"))
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute_plan(plan, "run_browser_use", state_dir=Path(tmp))
                payload = result.to_dict()
                self.assertEqual(payload["status"], "complete")
                self.assertEqual(payload["provider"], "browser-use")
                self.assertEqual(payload["artifacts"][0]["type"], "provider_output")
                self.assertIn("Browser Use SDK run returned", payload["verification"]["checks"])
        finally:
            if old_key is None:
                os.environ.pop("BROWSER_USE_API_KEY", None)
            else:
                os.environ["BROWSER_USE_API_KEY"] = old_key

    def test_browser_use_adapter_failed_payload_is_failed(self):
        old_key = os.environ.get("BROWSER_USE_API_KEY")
        os.environ["BROWSER_USE_API_KEY"] = "test-key"
        package = types.ModuleType("browser_use_sdk")
        v3 = types.ModuleType("browser_use_sdk.v3")

        class FakeResult:
            status = "failed"
            error = "browser task failed"

        class FakeAsyncBrowserUse:
            async def run(self, prompt):
                return FakeResult()

        v3.AsyncBrowserUse = FakeAsyncBrowserUse
        try:
            with patch.dict(sys.modules, {"browser_use_sdk": package, "browser_use_sdk.v3": v3}):
                plan = build_plan(infer_task("Search Facebook Ads Library behind Cloudflare and extract advertisers"))
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute_plan(plan, "run_browser_use_failed_payload", state_dir=Path(tmp), use_fallbacks=False)
            payload = result.to_dict()
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["provider"], "browser-use")
            self.assertIn("browser task failed", payload["error"])
            self.assertIn("provider payload checked for explicit failure", payload["verification"]["checks"])
        finally:
            if old_key is None:
                os.environ.pop("BROWSER_USE_API_KEY", None)
            else:
                os.environ["BROWSER_USE_API_KEY"] = old_key

    def test_orgo_adapter_with_fake_http(self):
        old_key = os.environ.get("ORGO_API_KEY")
        old_id = os.environ.get("ORGO_COMPUTER_ID")
        old_model = os.environ.get("ORGO_MODEL")
        os.environ["ORGO_API_KEY"] = "orgo-test-key"
        os.environ["ORGO_COMPUTER_ID"] = "computer_123"
        os.environ["ORGO_MODEL"] = "test-computer-model"
        calls = []

        def fake_http_json(url, body, headers, method="POST", timeout_seconds=None):
            calls.append((url, body, method, headers))
            self.assertEqual(headers["Authorization"], "Bearer orgo-test-key")
            if url.endswith("/v1/chat/completions"):
                self.assertEqual(body["model"], "test-computer-model")
                self.assertEqual(body["computer_id"], "computer_123")
                self.assertEqual(body["stream"], False)
                self.assertIn("Use a desktop computer to inspect files", body["messages"][0]["content"])
                return {"id": "chatcmpl_123", "choices": [{"message": {"content": "done"}}]}
            if url.endswith("/screenshot"):
                return {"image": "base64-image"}
            raise AssertionError(url)

        try:
            with patch("super_browser.adapters._http_json", side_effect=fake_http_json):
                plan = build_plan(infer_task("Use a desktop computer to inspect files"))
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute_plan(plan, "run_orgo", state_dir=Path(tmp))
            payload = result.to_dict()
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(payload["provider"], "orgo")
            self.assertEqual(payload["artifacts"][0]["computer_id"], "computer_123")
            self.assertEqual(payload["artifacts"][0]["type"], "provider_output")
            self.assertIn("/v1/chat/completions", calls[0][0])
            self.assertNotIn("/bash", calls[0][0])
            self.assertIn("submitted Orgo computer-use agent task", payload["verification"]["checks"])
            self.assertIn("requested screenshot", payload["verification"]["checks"])
        finally:
            if old_key is None:
                os.environ.pop("ORGO_API_KEY", None)
            else:
                os.environ["ORGO_API_KEY"] = old_key
            if old_id is None:
                os.environ.pop("ORGO_COMPUTER_ID", None)
            else:
                os.environ["ORGO_COMPUTER_ID"] = old_id
            if old_model is None:
                os.environ.pop("ORGO_MODEL", None)
            else:
                os.environ["ORGO_MODEL"] = old_model

    def test_orgo_adapter_auto_discovers_computer_when_id_unset(self):
        old_key = os.environ.get("ORGO_API_KEY")
        old_id = os.environ.get("ORGO_COMPUTER_ID")
        os.environ["ORGO_API_KEY"] = "orgo-test-key"
        os.environ.pop("ORGO_COMPUTER_ID", None)
        calls = []

        def fake_http_json(url, body, headers, method="POST", timeout_seconds=None):
            calls.append((url, body, method))
            self.assertEqual(headers["Authorization"], "Bearer orgo-test-key")
            if url.endswith("/workspaces") and method == "GET":
                return []
            if url.endswith("/workspaces") and method == "POST":
                self.assertEqual(body["name"], "super-browser")
                return {"id": "ws_auto", "name": "super-browser"}
            if url.endswith("/workspaces/ws_auto") and method == "GET":
                return {"id": "ws_auto", "desktops": []}
            if url.endswith("/computers") and method == "POST":
                self.assertEqual(body["workspace_id"], "ws_auto")
                self.assertEqual(body["name"], "super-browser-agent")
                self.assertEqual(body["auto_stop_minutes"], 30)
                return {"id": "computer_auto", "status": "running"}
            if url.endswith("/v1/chat/completions"):
                self.assertEqual(body["computer_id"], "computer_auto")
                return {"id": "chatcmpl_auto", "choices": [{"message": {"content": "done"}}]}
            if url.endswith("/screenshot"):
                return {"image": "base64-image"}
            raise AssertionError(url)

        try:
            with patch("super_browser.adapters._http_json", side_effect=fake_http_json):
                plan = build_plan(infer_task("Use a desktop computer to inspect files"))
                plan.primary_provider = "orgo"
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute_plan(plan, "run_orgo_auto_discover", state_dir=Path(tmp), use_fallbacks=False)
            payload = result.to_dict()
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(payload["provider"], "orgo")
            self.assertEqual(payload["artifacts"][0]["computer_id"], "computer_auto")
            self.assertTrue(any(check.startswith("computer: created computer") for check in payload["verification"]["checks"]))
        finally:
            if old_key is None:
                os.environ.pop("ORGO_API_KEY", None)
            else:
                os.environ["ORGO_API_KEY"] = old_key
            if old_id is not None:
                os.environ["ORGO_COMPUTER_ID"] = old_id

    def test_orgo_adapter_reuses_running_computer_when_id_unset(self):
        old_key = os.environ.get("ORGO_API_KEY")
        old_id = os.environ.get("ORGO_COMPUTER_ID")
        os.environ["ORGO_API_KEY"] = "orgo-test-key"
        os.environ.pop("ORGO_COMPUTER_ID", None)

        def fake_http_json(url, body, headers, method="POST", timeout_seconds=None):
            if url.endswith("/workspaces") and method == "GET":
                return {"projects": [{"id": "ws_1", "name": "super-browser"}]}
            if url.endswith("/workspaces/ws_1") and method == "GET":
                return {"id": "ws_1", "desktops": [{"id": "computer_run", "name": "super-browser-agent", "status": "running"}]}
            if url.endswith("/v1/chat/completions"):
                self.assertEqual(body["computer_id"], "computer_run")
                return {"id": "chatcmpl_reuse", "choices": [{"message": {"content": "done"}}]}
            if url.endswith("/screenshot"):
                return {"image": "base64-image"}
            raise AssertionError(url)

        try:
            with patch("super_browser.adapters._http_json", side_effect=fake_http_json):
                plan = build_plan(infer_task("Use a desktop computer to inspect files"))
                plan.primary_provider = "orgo"
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute_plan(plan, "run_orgo_reuse", state_dir=Path(tmp), use_fallbacks=False)
            payload = result.to_dict()
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(payload["artifacts"][0]["computer_id"], "computer_run")
            self.assertTrue(any("reused running computer" in check for check in payload["verification"]["checks"]))
        finally:
            if old_key is None:
                os.environ.pop("ORGO_API_KEY", None)
            else:
                os.environ["ORGO_API_KEY"] = old_key
            if old_id is not None:
                os.environ["ORGO_COMPUTER_ID"] = old_id

    def test_orgo_adapter_chat_exception_is_failed_result(self):
        old_key = os.environ.get("ORGO_API_KEY")
        old_id = os.environ.get("ORGO_COMPUTER_ID")
        os.environ["ORGO_API_KEY"] = "orgo-test-key"
        os.environ["ORGO_COMPUTER_ID"] = "computer_123"

        def fake_http_json(url, body, headers, method="POST", timeout_seconds=None):
            raise RuntimeError("orgo api unavailable")

        try:
            with patch("super_browser.adapters._http_json", side_effect=fake_http_json):
                plan = build_plan(infer_task("Use a desktop computer to inspect files"))
                plan.primary_provider = "orgo"
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute_plan(plan, "run_orgo_chat_failed", state_dir=Path(tmp), use_fallbacks=False)
            payload = result.to_dict()
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["provider"], "orgo")
            self.assertIn("orgo api unavailable", payload["error"])
            self.assertEqual(payload["artifacts"][0]["type"], "provider_docs")
            self.assertIn("provider request failed", payload["verification"]["checks"])
        finally:
            if old_key is None:
                os.environ.pop("ORGO_API_KEY", None)
            else:
                os.environ["ORGO_API_KEY"] = old_key
            if old_id is None:
                os.environ.pop("ORGO_COMPUTER_ID", None)
            else:
                os.environ["ORGO_COMPUTER_ID"] = old_id

    def test_orgo_adapter_screenshot_exception_is_failed_result(self):
        old_key = os.environ.get("ORGO_API_KEY")
        old_id = os.environ.get("ORGO_COMPUTER_ID")
        os.environ["ORGO_API_KEY"] = "orgo-test-key"
        os.environ["ORGO_COMPUTER_ID"] = "computer_123"

        def fake_http_json(url, body, headers, method="POST", timeout_seconds=None):
            self.assertEqual(headers["Authorization"], "Bearer orgo-test-key")
            if url.endswith("/v1/chat/completions"):
                return {"id": "chatcmpl_123", "choices": [{"message": {"content": "done"}}]}
            if url.endswith("/screenshot"):
                raise RuntimeError("screenshot unavailable")
            raise AssertionError(url)

        try:
            with patch("super_browser.adapters._http_json", side_effect=fake_http_json):
                plan = build_plan(infer_task("Use a desktop computer to inspect files"))
                plan.primary_provider = "orgo"
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute_plan(plan, "run_orgo_screenshot_failed", state_dir=Path(tmp), use_fallbacks=False)
            payload = result.to_dict()
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["provider"], "orgo")
            self.assertIn("screenshot unavailable", payload["error"])
            self.assertEqual(payload["artifacts"][0]["type"], "provider_output")
            self.assertIn("screenshot request failed", payload["verification"]["checks"])
            self.assertIn("provider payload checked for explicit failure", payload["verification"]["checks"])
        finally:
            if old_key is None:
                os.environ.pop("ORGO_API_KEY", None)
            else:
                os.environ["ORGO_API_KEY"] = old_key
            if old_id is None:
                os.environ.pop("ORGO_COMPUTER_ID", None)
            else:
                os.environ["ORGO_COMPUTER_ID"] = old_id

    def test_airtop_adapter_with_fake_http(self):
        old_key = os.environ.get("AIRTOP_API_KEY")
        old_timeout = os.environ.get("AIRTOP_TIMEOUT_MINUTES")
        os.environ["AIRTOP_API_KEY"] = "airtop-test-key"
        os.environ["AIRTOP_TIMEOUT_MINUTES"] = "1"
        calls = []

        def fake_http_json(url, body, headers, method="POST", timeout_seconds=None):
            calls.append((url, body, method, headers))
            self.assertEqual(headers["Authorization"], "Bearer airtop-test-key")
            if method == "DELETE":
                return {}
            if method == "GET" and url.endswith("/sessions/session_123"):
                return {"data": {"id": "session_123", "status": "running"}}
            if url.endswith("/sessions"):
                return {"data": {"id": "session_123", "status": "initializing"}}
            if url.endswith("/sessions/session_123/windows"):
                self.assertEqual(body["url"], "https://example.com")
                return {"data": {"windowId": "window_123", "targetId": "target_123"}}
            if url.endswith("/sessions/session_123/windows/window_123/page-query"):
                return {"data": {"modelResponse": "Airtop answer"}, "meta": {"usage": {"credits": 1}}}
            raise AssertionError(url)

        try:
            with patch("super_browser.adapters._http_json", side_effect=fake_http_json):
                plan = build_plan(infer_task("Extract the page summary", url="https://example.com"))
                plan.primary_provider = "airtop"
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute_plan(plan, "run_airtop", state_dir=Path(tmp))
            payload = result.to_dict()
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(payload["provider"], "airtop")
            self.assertEqual(payload["artifacts"][0]["session_id"], "session_123")
            self.assertIn("queried Airtop page", payload["verification"]["checks"])
            self.assertEqual(calls[-1][2], "DELETE")
        finally:
            if old_key is None:
                os.environ.pop("AIRTOP_API_KEY", None)
            else:
                os.environ["AIRTOP_API_KEY"] = old_key
            if old_timeout is None:
                os.environ.pop("AIRTOP_TIMEOUT_MINUTES", None)
            else:
                os.environ["AIRTOP_TIMEOUT_MINUTES"] = old_timeout

    def test_airtop_adapter_failed_query_payload_is_failed_and_saved(self):
        old_key = os.environ.get("AIRTOP_API_KEY")
        old_timeout = os.environ.get("AIRTOP_TIMEOUT_MINUTES")
        os.environ["AIRTOP_API_KEY"] = "airtop-test-key"
        os.environ["AIRTOP_TIMEOUT_MINUTES"] = "1"
        calls = []

        def fake_http_json(url, body, headers, method="POST", timeout_seconds=None):
            calls.append((url, body, method, headers))
            self.assertEqual(headers["Authorization"], "Bearer airtop-test-key")
            if method == "DELETE":
                return {}
            if method == "GET" and url.endswith("/sessions/session_123"):
                return {"data": {"id": "session_123", "status": "running"}}
            if url.endswith("/sessions"):
                return {"data": {"id": "session_123", "status": "initializing"}}
            if url.endswith("/sessions/session_123/windows"):
                return {"data": {"windowId": "window_123"}}
            if url.endswith("/sessions/session_123/windows/window_123/page-query"):
                return {"data": {"status": "failed", "error": "page query timeout"}}
            raise AssertionError(url)

        try:
            with patch("super_browser.adapters._http_json", side_effect=fake_http_json):
                plan = build_plan(infer_task("Extract the page summary", url="https://example.com"))
                plan.primary_provider = "airtop"
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute_plan(plan, "run_airtop_failed_payload", state_dir=Path(tmp), use_fallbacks=False)
            payload = result.to_dict()
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["provider"], "airtop")
            self.assertIn("page query timeout", payload["error"])
            self.assertEqual(payload["artifacts"][0]["type"], "provider_output")
            self.assertEqual(calls[-1][2], "DELETE")
            self.assertIn("provider payload checked for explicit failure", payload["verification"]["checks"])
        finally:
            if old_key is None:
                os.environ.pop("AIRTOP_API_KEY", None)
            else:
                os.environ["AIRTOP_API_KEY"] = old_key
            if old_timeout is None:
                os.environ.pop("AIRTOP_TIMEOUT_MINUTES", None)
            else:
                os.environ["AIRTOP_TIMEOUT_MINUTES"] = old_timeout

    def test_hyperbrowser_adapter_with_fake_http(self):
        old_key = os.environ.get("HYPERBROWSER_API_KEY")
        old_sleep = os.environ.get("HYPERBROWSER_POLL_SECONDS")
        old_attempts = os.environ.get("HYPERBROWSER_POLL_ATTEMPTS")
        os.environ["HYPERBROWSER_API_KEY"] = "hyperbrowser-test-key"
        os.environ["HYPERBROWSER_POLL_SECONDS"] = "0"
        os.environ["HYPERBROWSER_POLL_ATTEMPTS"] = "2"
        calls = []

        def fake_http_json(url, body, headers, method="POST", timeout_seconds=None):
            calls.append((url, method))
            self.assertEqual(headers["x-api-key"], "hyperbrowser-test-key")
            if method == "POST" and url.endswith("/scrape"):
                self.assertEqual(body["url"], "https://example.com")
                return {"jobId": "job_123"}
            if method == "GET" and url.endswith("/scrape/job_123/status"):
                return {"status": "completed"}
            if method == "GET" and url.endswith("/scrape/job_123"):
                return {"jobId": "job_123", "status": "completed", "data": {"markdown": "ok"}}
            raise AssertionError(url)

        try:
            with patch("super_browser.adapters._http_json", side_effect=fake_http_json):
                plan = build_plan(infer_task("Extract the public page", url="https://example.com"))
                plan.primary_provider = "hyperbrowser"
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute_plan(plan, "run_hyperbrowser", state_dir=Path(tmp))
            payload = result.to_dict()
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(payload["provider"], "hyperbrowser")
            self.assertEqual(payload["artifacts"][0]["job_id"], "job_123")
            self.assertIn("submitted Hyperbrowser scrape", payload["verification"]["checks"])
            self.assertEqual([method for _, method in calls], ["POST", "GET", "GET"])
            self.assertTrue(calls[1][0].endswith("/scrape/job_123/status"))
            self.assertTrue(calls[2][0].endswith("/scrape/job_123"))
        finally:
            if old_key is None:
                os.environ.pop("HYPERBROWSER_API_KEY", None)
            else:
                os.environ["HYPERBROWSER_API_KEY"] = old_key
            if old_sleep is None:
                os.environ.pop("HYPERBROWSER_POLL_SECONDS", None)
            else:
                os.environ["HYPERBROWSER_POLL_SECONDS"] = old_sleep
            if old_attempts is None:
                os.environ.pop("HYPERBROWSER_POLL_ATTEMPTS", None)
            else:
                os.environ["HYPERBROWSER_POLL_ATTEMPTS"] = old_attempts

    def test_hyperbrowser_adapter_unfinished_payload_is_failed(self):
        old_key = os.environ.get("HYPERBROWSER_API_KEY")
        old_sleep = os.environ.get("HYPERBROWSER_POLL_SECONDS")
        old_attempts = os.environ.get("HYPERBROWSER_POLL_ATTEMPTS")
        os.environ["HYPERBROWSER_API_KEY"] = "hyperbrowser-test-key"
        os.environ["HYPERBROWSER_POLL_SECONDS"] = "0"
        os.environ["HYPERBROWSER_POLL_ATTEMPTS"] = "1"
        calls = []

        def fake_http_json(url, body, headers, method="POST", timeout_seconds=None):
            calls.append((url, method))
            self.assertEqual(headers["x-api-key"], "hyperbrowser-test-key")
            if method == "POST" and url.endswith("/scrape"):
                return {"jobId": "job_123"}
            if method == "GET" and url.endswith("/scrape/job_123/status"):
                return {"jobId": "job_123", "status": "running"}
            raise AssertionError(url)

        try:
            with patch("super_browser.adapters._http_json", side_effect=fake_http_json):
                plan = build_plan(infer_task("Extract the public page", url="https://example.com"))
                plan.primary_provider = "hyperbrowser"
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute_plan(plan, "run_hyperbrowser_unfinished_payload", state_dir=Path(tmp), use_fallbacks=False)
            payload = result.to_dict()
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["provider"], "hyperbrowser")
            self.assertIn("unfinished status=running", payload["error"])
            self.assertIn("provider payload checked for explicit failure", payload["verification"]["checks"])
            self.assertEqual([method for _, method in calls], ["POST", "GET"])
            self.assertTrue(calls[1][0].endswith("/scrape/job_123/status"))
        finally:
            if old_key is None:
                os.environ.pop("HYPERBROWSER_API_KEY", None)
            else:
                os.environ["HYPERBROWSER_API_KEY"] = old_key
            if old_sleep is None:
                os.environ.pop("HYPERBROWSER_POLL_SECONDS", None)
            else:
                os.environ["HYPERBROWSER_POLL_SECONDS"] = old_sleep
            if old_attempts is None:
                os.environ.pop("HYPERBROWSER_POLL_ATTEMPTS", None)
            else:
                os.environ["HYPERBROWSER_POLL_ATTEMPTS"] = old_attempts

    def test_hyperbrowser_adapter_failed_status_is_failed(self):
        old_key = os.environ.get("HYPERBROWSER_API_KEY")
        old_sleep = os.environ.get("HYPERBROWSER_POLL_SECONDS")
        old_attempts = os.environ.get("HYPERBROWSER_POLL_ATTEMPTS")
        os.environ["HYPERBROWSER_API_KEY"] = "hyperbrowser-test-key"
        os.environ["HYPERBROWSER_POLL_SECONDS"] = "0"
        os.environ["HYPERBROWSER_POLL_ATTEMPTS"] = "2"

        def fake_http_json(url, body, headers, method="POST", timeout_seconds=None):
            self.assertEqual(headers["x-api-key"], "hyperbrowser-test-key")
            if method == "POST" and url.endswith("/scrape"):
                return {"jobId": "job_123"}
            if method == "GET" and url.endswith("/scrape/job_123/status"):
                return {"status": "failed"}
            if method == "GET" and url.endswith("/scrape/job_123"):
                return {"jobId": "job_123", "error": "scrape failed"}
            raise AssertionError(url)

        try:
            with patch("super_browser.adapters._http_json", side_effect=fake_http_json):
                plan = build_plan(infer_task("Extract the public page", url="https://example.com"))
                plan.primary_provider = "hyperbrowser"
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute_plan(plan, "run_hyperbrowser_failed_status", state_dir=Path(tmp), use_fallbacks=False)
            payload = result.to_dict()
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["provider"], "hyperbrowser")
            self.assertIn("scrape failed", payload["error"])
        finally:
            if old_key is None:
                os.environ.pop("HYPERBROWSER_API_KEY", None)
            else:
                os.environ["HYPERBROWSER_API_KEY"] = old_key
            if old_sleep is None:
                os.environ.pop("HYPERBROWSER_POLL_SECONDS", None)
            else:
                os.environ["HYPERBROWSER_POLL_SECONDS"] = old_sleep
            if old_attempts is None:
                os.environ.pop("HYPERBROWSER_POLL_ATTEMPTS", None)
            else:
                os.environ["HYPERBROWSER_POLL_ATTEMPTS"] = old_attempts

    def test_steel_adapter_with_fake_playwright(self):
        old_key = os.environ.get("STEEL_API_KEY")
        os.environ["STEEL_API_KEY"] = "steel-test-key"
        seen = {}

        class FakePage:
            def goto(self, *args, **kwargs):
                seen["goto"] = args[0]

            def title(self):
                return "Steel Fixture"

            def locator(self, selector):
                class Locator:
                    def inner_text(self, timeout):
                        return "steel fixture text"

                return Locator()

            def screenshot(self, path, full_page):
                Path(path).write_bytes(b"fakepng")

        class FakeContext:
            pages = [FakePage()]

        class FakeBrowser:
            contexts = [FakeContext()]

            def close(self):
                seen["close_attempted"] = True
                raise RuntimeError("steel close failed after capture")

        class FakeChromium:
            def connect_over_cdp(self, url):
                seen["cdp_url"] = url
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        playwright_module = types.ModuleType("playwright")
        sync_api_module = types.ModuleType("playwright.sync_api")
        sync_api_module.Error = Exception
        sync_api_module.sync_playwright = lambda: FakePlaywright()

        http_calls = []

        def fake_http_json(url, body, headers, method="POST", timeout_seconds=None):
            http_calls.append({"url": url, "body": body, "headers": headers, "method": method})
            if url.endswith("/sessions"):
                return {"id": "11111111-2222-3333-4444-555555555555"}
            if url.endswith("/release"):
                return {}
            raise AssertionError(f"unexpected steel http call: {url}")

        try:
            with patch.dict(sys.modules, {"playwright": playwright_module, "playwright.sync_api": sync_api_module}):
                with patch("super_browser.adapters._http_json", side_effect=fake_http_json):
                    plan = build_plan(infer_task("Extract a cloud browser page", url="https://example.com"))
                    plan.primary_provider = "steel"
                    with tempfile.TemporaryDirectory() as tmp:
                        result = execute_plan(plan, "run_steel", state_dir=Path(tmp))
            payload = result.to_dict()
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(payload["provider"], "steel")
            self.assertEqual(seen["goto"], "https://example.com")
            self.assertIn("apiKey=steel-test-key", seen["cdp_url"])
            self.assertIn("sessionId=11111111-2222-3333-4444-555555555555", seen["cdp_url"])
            self.assertEqual(http_calls[0]["url"], "https://api.steel.dev/v1/sessions")
            self.assertEqual(http_calls[0]["headers"]["steel-api-key"], "steel-test-key")
            self.assertEqual(
                http_calls[-1]["url"],
                "https://api.steel.dev/v1/sessions/11111111-2222-3333-4444-555555555555/release",
            )
            self.assertIn("connected to Steel over CDP", payload["verification"]["checks"])
            self.assertTrue(seen["close_attempted"])
            self.assertIn("browser close failed after capture", payload["verification"]["checks"])
            self.assertIn("browser_close_failed", [event.get("reason") for event in payload["events"]])
        finally:
            if old_key is None:
                os.environ.pop("STEEL_API_KEY", None)
            else:
                os.environ["STEEL_API_KEY"] = old_key

    def test_provider_transport_blocks_private_base_url_override_before_credentials_are_sent(self):
        old_key = os.environ.get("HYPERBROWSER_API_KEY")
        old_base = os.environ.get("HYPERBROWSER_API_BASE")
        old_allow_internal = os.environ.get("SUPER_BROWSER_ALLOW_INTERNAL_PROVIDER_BASES")
        os.environ["HYPERBROWSER_API_KEY"] = "hyperbrowser-test-key"
        os.environ["HYPERBROWSER_API_BASE"] = "https://10.0.0.5"
        os.environ.pop("SUPER_BROWSER_ALLOW_INTERNAL_PROVIDER_BASES", None)
        try:
            plan = build_plan(infer_task("Scrape the hosted browser page", url="https://93.184.216.34", providers_allowed=["hyperbrowser"]))
            with tempfile.TemporaryDirectory() as tmp:
                with patch("super_browser.adapters._http_json") as http_mock:
                    result = execute_plan(plan, "run_hyperbrowser_private_base_guard", state_dir=Path(tmp), use_fallbacks=False)
                payload = result.to_dict()
                self.assertEqual(payload["status"], "blocked")
                self.assertFalse(http_mock.called)
                self.assertEqual(payload["provider"], "hyperbrowser")
                self.assertIn("provider_transport_target_scope", [event["reason"] for event in payload["events"] if event["type"] == "blocked"])
                self.assertIn("provider transport override was inspected before credentials were sent", payload["verification"]["checks"])
                metadata_artifact = next(item for item in payload["artifacts"] if item["type"] == "metadata")
                with open(metadata_artifact["path"], encoding="utf-8") as handle:
                    metadata = json.load(handle)
                self.assertEqual(metadata["env_name"], "HYPERBROWSER_API_BASE")
                self.assertEqual(metadata["reason"], "provider_transport_target_scope")
                self.assertEqual(metadata["evidence"]["target_evidence"]["target_scope"], "private_network")
        finally:
            if old_key is None:
                os.environ.pop("HYPERBROWSER_API_KEY", None)
            else:
                os.environ["HYPERBROWSER_API_KEY"] = old_key
            if old_base is None:
                os.environ.pop("HYPERBROWSER_API_BASE", None)
            else:
                os.environ["HYPERBROWSER_API_BASE"] = old_base
            if old_allow_internal is None:
                os.environ.pop("SUPER_BROWSER_ALLOW_INTERNAL_PROVIDER_BASES", None)
            else:
                os.environ["SUPER_BROWSER_ALLOW_INTERNAL_PROVIDER_BASES"] = old_allow_internal

    def test_provider_transport_allows_loopback_base_url_override(self):
        old_key = os.environ.get("HYPERBROWSER_API_KEY")
        old_base = os.environ.get("HYPERBROWSER_API_BASE")
        old_poll = os.environ.get("HYPERBROWSER_POLL_SECONDS")
        os.environ["HYPERBROWSER_API_KEY"] = "hyperbrowser-test-key"
        os.environ["HYPERBROWSER_API_BASE"] = "http://127.0.0.1:3000"
        os.environ["HYPERBROWSER_POLL_SECONDS"] = "0"
        captured = {}

        def fake_http_json(url, body, headers, method="POST", timeout_seconds=None):
            captured.setdefault("first_url", url)
            if url.endswith("/scrape") and method == "POST":
                return {"jobId": "job_loopback"}
            if url.endswith("/scrape/job_loopback/status"):
                return {"status": "completed"}
            return {"jobId": "job_loopback", "status": "completed", "data": {"markdown": "Hyperbrowser fixture"}}

        try:
            plan = build_plan(infer_task("Scrape the hosted browser page", url="https://93.184.216.34", providers_allowed=["hyperbrowser"]))
            with tempfile.TemporaryDirectory() as tmp:
                with patch("super_browser.adapters._http_json", side_effect=fake_http_json):
                    result = execute_plan(plan, "run_hyperbrowser_loopback_base", state_dir=Path(tmp), use_fallbacks=False)
            payload = result.to_dict()
            self.assertEqual(payload["status"], "complete")
            self.assertTrue(captured["first_url"].startswith("http://127.0.0.1:3000/scrape"))
        finally:
            if old_key is None:
                os.environ.pop("HYPERBROWSER_API_KEY", None)
            else:
                os.environ["HYPERBROWSER_API_KEY"] = old_key
            if old_base is None:
                os.environ.pop("HYPERBROWSER_API_BASE", None)
            else:
                os.environ["HYPERBROWSER_API_BASE"] = old_base
            if old_poll is None:
                os.environ.pop("HYPERBROWSER_POLL_SECONDS", None)
            else:
                os.environ["HYPERBROWSER_POLL_SECONDS"] = old_poll

    def test_provider_transport_blocks_base_url_credentials_before_credentials_are_sent(self):
        old_key = os.environ.get("HYPERBROWSER_API_KEY")
        old_base = os.environ.get("HYPERBROWSER_API_BASE")
        os.environ["HYPERBROWSER_API_KEY"] = "hyperbrowser-test-key"
        os.environ["HYPERBROWSER_API_BASE"] = "https://user:pass@hyperbrowser.example"
        try:
            plan = build_plan(infer_task("Scrape the hosted browser page", url="https://93.184.216.34", providers_allowed=["hyperbrowser"]))
            with tempfile.TemporaryDirectory() as tmp:
                with patch("super_browser.adapters._http_json") as http_mock:
                    result = execute_plan(plan, "run_hyperbrowser_base_credentials_guard", state_dir=Path(tmp), use_fallbacks=False)
                payload = result.to_dict()
                self.assertEqual(payload["status"], "blocked")
                self.assertFalse(http_mock.called)
                self.assertIn("invalid_provider_transport_url", [event["reason"] for event in payload["events"] if event["type"] == "blocked"])
                self.assertIn("provider transport URL must not contain username or password credentials", payload["error"])
        finally:
            if old_key is None:
                os.environ.pop("HYPERBROWSER_API_KEY", None)
            else:
                os.environ["HYPERBROWSER_API_KEY"] = old_key
            if old_base is None:
                os.environ.pop("HYPERBROWSER_API_BASE", None)
            else:
                os.environ["HYPERBROWSER_API_BASE"] = old_base


if __name__ == "__main__":
    unittest.main()
