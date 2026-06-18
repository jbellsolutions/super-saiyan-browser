import json
import io
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from super_browser.adapters import execute_plan
from super_browser.cli import main as cli_main
from super_browser.mcp_server import handle_tool
from super_browser.models import ExecutionResult, RunState, action_fingerprint_from_plan, plan_fingerprint, utc_now
from super_browser.redaction import REDACTED, redact, redact_headers, redact_text, safe_json_dumps
from super_browser.router import build_plan, infer_task
from super_browser.runtime import create_run


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
from super_browser.store import RunStore
from super_browser.verifier import verify_run_payload


class _SecretHeaderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", "sessionid=needle-cookie")
        self.send_header("X-Api-Key", "needle-api-key")
        self.send_header("Authorization", "Bearer needle-auth-header")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class _SecretBodyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            b'{"ok": true, '
            b'"access_token": "needle-body-token", '
            b'"url": "https://trace.example/path?token=needle-body-query", '
            b'"session_id": "session_123"}'
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class RedactionTests(unittest.TestCase):
    def test_redacts_nested_payloads_headers_urls_and_error_text(self):
        payload = {
            "Authorization": "Bearer needle-bearer",
            "Set-Cookie": "sessionid=needle-cookie",
            "url": "https://example.test/path?token=needle-query&safe=1",
            "nested": {
                "access_token": "needle-access-token",
                "message": "STEEL_API_KEY=needle-env and Bearer needle-inline-token",
                "session_id": "session_123",
            },
        }
        redacted = redact(payload)
        dumped = safe_json_dumps(payload)

        self.assertEqual(redacted["Authorization"], REDACTED)
        self.assertEqual(redacted["Set-Cookie"], REDACTED)
        self.assertEqual(redacted["nested"]["access_token"], REDACTED)
        self.assertEqual(redacted["nested"]["session_id"], "session_123")
        self.assertIn("token=[REDACTED]", redacted["url"])
        self.assertNotIn("needle-", dumped)

    def test_redacts_url_userinfo_credentials(self):
        text = "Open https://agent:needle-url-password@example.test/private?token=needle-query-token"
        redacted = redact_text(text)
        dumped = safe_json_dumps({"url": "https://agent:needle-url-password@example.test/private"})

        self.assertEqual(redacted, "Open https://[REDACTED]@example.test/private?token=[REDACTED]")
        self.assertIn("https://[REDACTED]@example.test/private", dumped)
        self.assertNotIn("needle-url-password", redacted)
        self.assertNotIn("needle-url-password", dumped)

    def test_redacts_sensitive_response_headers_in_raw_http_metadata(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SecretHeaderHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                url = f"http://127.0.0.1:{server.server_port}/data.json"
                run = create_run("Fetch this JSON endpoint through raw HTTP", url=url)
                metadata_artifact = next(item for item in run.artifacts if item["type"] == "metadata")
                metadata = json.loads(Path(metadata_artifact["path"]).read_text(encoding="utf-8"))
                dumped = json.dumps(metadata)

                self.assertEqual(metadata["headers"]["Set-Cookie"], REDACTED)
                self.assertEqual(metadata["headers"]["X-Api-Key"], REDACTED)
                self.assertEqual(metadata["headers"]["Authorization"], REDACTED)
                self.assertNotIn("needle-cookie", dumped)
                self.assertNotIn("needle-api-key", dumped)
                self.assertNotIn("needle-auth-header", dumped)
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            server.shutdown()
            server.server_close()

    def test_redacts_sensitive_raw_http_response_body_artifact(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SecretBodyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                url = f"http://127.0.0.1:{server.server_port}/data.json"
                run = create_run("Fetch this JSON endpoint through raw HTTP", url=url)
                response_artifact = next(item for item in run.artifacts if item["type"] == "http_response")
                metadata_artifact = next(item for item in run.artifacts if item["type"] == "metadata")
                report_artifact = next(item for item in run.artifacts if item["type"] == "run_report")

                response_body = json.loads(Path(response_artifact["path"]).read_text(encoding="utf-8"))
                metadata = json.loads(Path(metadata_artifact["path"]).read_text(encoding="utf-8"))
                run_report = json.loads(Path(report_artifact["path"]).read_text(encoding="utf-8"))
                dumped = json.dumps({"run": run.to_dict(), "body": response_body, "metadata": metadata, "report": run_report})

                self.assertEqual(response_body["access_token"], REDACTED)
                self.assertEqual(response_body["session_id"], "session_123")
                self.assertEqual(response_body["url"], "https://trace.example/path?token=[REDACTED]")
                self.assertTrue(response_artifact["body_redacted"])
                self.assertTrue(metadata["body_redacted"])
                self.assertNotIn("needle-body-token", dumped)
                self.assertNotIn("needle-body-query", dumped)
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            server.shutdown()
            server.server_close()

    def test_redacts_provider_output_without_breaking_session_ids(self):
        old_key = os.environ.get("HYPERBROWSER_API_KEY")
        old_poll = os.environ.get("HYPERBROWSER_POLL_SECONDS")
        os.environ["HYPERBROWSER_API_KEY"] = "needle-hyperbrowser-env"
        os.environ["HYPERBROWSER_POLL_SECONDS"] = "0"

        class FakeResponse:
            def read(self):
                return (
                    b'{"jobId": "job_123", "status": "completed", '
                    b'"access_token": "needle-provider-token", '
                    b'"url": "https://trace.example/watch?token=needle-trace-token", '
                    b'"session_id": "session_123"}'
                )

        try:
            with patch("super_browser.adapters.urlopen", return_value=FakeResponse()):
                plan = build_plan(infer_task("Read https://example.com and report the page title", url="https://example.com", providers_allowed=["hyperbrowser"]))
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute_plan(plan, "run_hyperbrowser_redaction", state_dir=Path(tmp), use_fallbacks=False, approval_context=_approval_context_for(plan))
                    payload = result.to_dict()
                    output_path = Path(payload["artifacts"][0]["path"])
                    output = json.loads(output_path.read_text(encoding="utf-8"))
            dumped = json.dumps(output)
            self.assertEqual(output["result"]["access_token"], REDACTED)
            self.assertEqual(output["result"]["session_id"], "session_123")
            self.assertNotIn("needle-provider-token", dumped)
            self.assertNotIn("needle-trace-token", dumped)
        finally:
            if old_key is None:
                os.environ.pop("HYPERBROWSER_API_KEY", None)
            else:
                os.environ["HYPERBROWSER_API_KEY"] = old_key
            if old_poll is None:
                os.environ.pop("HYPERBROWSER_POLL_SECONDS", None)
            else:
                os.environ["HYPERBROWSER_POLL_SECONDS"] = old_poll

    def test_execution_result_redacts_adapter_level_errors_and_metadata(self):
        result = ExecutionResult(
            provider="hyperbrowser",
            status="failed",
            error="Hyperbrowser failed at https://example.test/scrape?token=needle-error-token with Bearer needle-error-auth",
            artifacts=[
                {
                    "type": "provider_output",
                    "url": "https://trace.example/watch?token=needle-artifact-token",
                    "session_id": "session_123",
                }
            ],
            events=[{"type": "failed", "message": "STEEL_API_KEY=needle-event-key"}],
            verification={"checks": ["Bearer needle-verification-auth"]},
        )

        payload = result.to_dict()
        dumped = json.dumps(payload)

        self.assertEqual(payload["artifacts"][0]["session_id"], "session_123")
        self.assertIn("token=[REDACTED]", payload["error"])
        self.assertNotIn("needle-error-token", dumped)
        self.assertNotIn("needle-error-auth", dumped)
        self.assertNotIn("needle-artifact-token", dumped)
        self.assertNotIn("needle-event-key", dumped)
        self.assertNotIn("needle-verification-auth", dumped)

    def test_verifier_redacts_trace_links(self):
        payload = {
            "run_id": "run_trace_secret",
            "status": "planned",
            "plan": {"primary_provider": "browser-use", "fallback_providers": []},
            "artifacts": [{"type": "recording_url", "provider": "browser-use", "url": "https://trace.example/watch?token=needle-trace"}],
            "approvals": [],
            "verification": {"confidence": "low", "selected_provider": "browser-use"},
        }
        report = verify_run_payload(payload)
        self.assertEqual(report["trace_links"][0]["url"], "https://trace.example/watch?token=[REDACTED]")
        self.assertNotIn("needle-trace", json.dumps(report))

    def test_store_redacts_run_payload_before_persisting(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            store = RunStore()
            run = RunState.create(build_plan(infer_task("Extract a page")), status="planned")
            run.events.append({"type": "execution_error", "message": "Provider returned Bearer needle-store-auth"})
            run.artifacts.append({"type": "recording_url", "url": "https://trace.example/watch?token=needle-store-token"})
            store.save(run)

            loaded = store.get(run.run_id)
            dumped = json.dumps(loaded)
            self.assertNotIn("needle-store-auth", dumped)
            self.assertNotIn("needle-store-token", dumped)
            self.assertIn(REDACTED, dumped)

    def test_redact_headers_keeps_safe_values(self):
        headers = redact_headers({"Content-Type": "application/json", "Cookie": "needle-cookie"})
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Cookie"], REDACTED)

    def test_cli_and_mcp_plan_outputs_are_redacted(self):
        secret_url = "https://example.test/path?token=needle-plan-token"

        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(["plan", "--goal", "Extract titles", "--url", secret_url])
        cli_payload = json.loads(output.getvalue())

        mcp_payload = handle_tool("plan_browser_task", {"goal": "Extract titles", "url": secret_url})

        self.assertEqual(code, 0)
        self.assertNotIn("needle-plan-token", json.dumps(cli_payload))
        self.assertNotIn("needle-plan-token", json.dumps(mcp_payload))
        self.assertEqual(cli_payload["task"]["url"], "https://example.test/path?token=[REDACTED]")
        self.assertEqual(mcp_payload["task"]["url"], "https://example.test/path?token=[REDACTED]")


if __name__ == "__main__":
    unittest.main()
