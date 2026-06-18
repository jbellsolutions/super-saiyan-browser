import json
import math
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from super_browser.models import ExecutionResult, plan_fingerprint
from super_browser.runtime import approve_run, create_run
from super_browser.store import RunStore
from super_browser.verifier import verify_run, verify_run_payload


class _JsonHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok": true, "name": "verify-fixture"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def _task_payload(**overrides):
    task = {
        "goal": "Extract titles from https://example.com",
        "url": "https://example.com",
        "target_scope": "public_web",
        "external_write": False,
        "requires_auth": False,
        "draft_only": False,
        "providers_allowed": [],
    }
    task.update(overrides)
    return task


def _artifact_path(state_dir: str, run_id: str, filename: str) -> Path:
    path = Path(state_dir) / "artifacts" / run_id / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class VerifierTests(unittest.TestCase):
    def test_verify_completed_run_reads_report_and_artifacts(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                url = f"http://127.0.0.1:{server.server_port}/data.json"
                run = create_run("Fetch this JSON endpoint through raw HTTP", url=url)
                report = verify_run(run.run_id)
                self.assertEqual(report["status"], "complete")
                self.assertEqual(report["confidence"], "high")
                self.assertEqual(report["selected_provider"], "decodo-http")
                self.assertEqual(report["cost_band"], "low")
                self.assertEqual(report["cost_estimate"]["primary"]["provider"], "decodo-http")
                self.assertEqual(report["budget_status"], "no_ceiling")
                self.assertIn("budget status: no_ceiling", report["checks"])
                self.assertEqual(report["failures"], [])
                self.assertIn("run-report.json parsed", report["checks"])
                self.assertTrue(any(check.startswith("artifact hashes verified:") for check in report["checks"]))
                self.assertGreaterEqual(report["artifact_audit"]["existing_count"], 2)
                self.assertGreaterEqual(report["artifact_audit"]["hash_checked_count"], 2)
                self.assertEqual(report["policy_guard"]["target_scope"], "loopback")
                self.assertEqual(report["policy_guard"]["approval_status"], "not_required")
                self.assertFalse(report["policy_guard"]["safety_stop"])
                self.assertEqual(report["plan_integrity"]["status"], "verified")
                self.assertIn("run-report plan fingerprint verified", report["checks"])
                self.assertIn("artifacts", report["run_report"])
                self.assertTrue(report["run_report"]["plan_sha256"])
                self.assertTrue(report["run_report"]["artifacts"][0]["sha256"])
                self.assertTrue(Path(report["verification_report_path"]).exists())
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            server.shutdown()
            server.server_close()

    def test_verify_payload_reports_missing_artifact_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            missing_path = str(Path(tmp) / "artifacts" / "run_missing" / "missing.txt")
            payload = {
                "run_id": "run_missing",
                "status": "complete",
                "plan": {"primary_provider": "playwright", "fallback_providers": [], "task": _task_payload()},
                "artifacts": [{"type": "text", "provider": "playwright", "path": missing_path}],
                "approvals": [],
                "verification": {"confidence": "high", "checks": ["captured body text"], "selected_provider": "playwright"},
            }
            report = verify_run_payload(payload)
            self.assertEqual(report["confidence"], "low")
            self.assertEqual(report["failures"][0]["type"], "missing_artifact_path")
            self.assertEqual(report["failures"][1]["type"], "missing_run_report")

    def test_verify_payload_reports_artifact_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            artifact_path = _artifact_path(tmp, "run_hash_mismatch", "artifact.txt")
            artifact_path.write_text("changed content", encoding="utf-8")
            payload = {
                "run_id": "run_hash_mismatch",
                "status": "planned",
                "plan": {"primary_provider": "playwright", "fallback_providers": [], "task": _task_payload()},
                "artifacts": [{"type": "text", "provider": "playwright", "path": str(artifact_path), "sha256": "0" * 64}],
                "approvals": [],
                "verification": {"confidence": "high", "checks": ["captured body text"], "selected_provider": "playwright"},
            }
            report = verify_run_payload(payload)
            self.assertEqual(report["confidence"], "low")
            self.assertEqual(report["failures"][0]["type"], "artifact_hash_mismatch")
            self.assertEqual(report["artifact_audit"]["hash_checked_count"], 1)
            self.assertFalse(report["artifact_audit"]["checked"][0]["hash_ok"])

    def test_verify_payload_does_not_read_untrusted_run_report_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = str(Path(tmp) / "state")
            forged_report_path = Path(tmp) / "outside-report.json"
            forged_report_path.write_text(
                json.dumps(
                    {
                        "run_id": "run_forged_path",
                        "plan_sha256": "0" * 64,
                        "final_provider": "playwright",
                        "final_status": "complete",
                        "secret": "needle-verifier-secret",
                    }
                ),
                encoding="utf-8",
            )
            payload = {
                "run_id": "run_forged_path",
                "status": "complete",
                "plan": {"primary_provider": "playwright", "fallback_providers": [], "task": _task_payload()},
                "artifacts": [{"type": "run_report", "provider": "playwright", "path": str(forged_report_path)}],
                "approvals": [],
                "verification": {"confidence": "high", "checks": [], "selected_provider": "playwright"},
            }

            report = verify_run_payload(payload)

            self.assertEqual(report["confidence"], "low")
            self.assertEqual(report["run_report"], None)
            self.assertEqual(report["run_report_path"], None)
            self.assertEqual(report["failures"][0]["type"], "untrusted_artifact_path")
            self.assertEqual(report["failures"][1]["type"], "missing_run_report")
            self.assertEqual(report["artifact_scope"]["untrusted_paths"][0]["artifact_type"], "run_report")
            self.assertNotIn("needle-verifier-secret", json.dumps(report))

    def test_verify_payload_rejects_dot_segment_run_id_before_reading_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = str(Path(tmp) / "state")
            forged_report_path = Path(tmp) / "state" / "run-report.json"
            forged_report_path.parent.mkdir(parents=True)
            forged_report_path.write_text(
                json.dumps(
                    {
                        "run_id": "..",
                        "plan_sha256": "0" * 64,
                        "final_provider": "playwright",
                        "final_status": "complete",
                        "secret": "needle-dot-run-id-secret",
                    }
                ),
                encoding="utf-8",
            )
            payload = {
                "run_id": "..",
                "status": "complete",
                "plan": {"primary_provider": "playwright", "fallback_providers": [], "task": _task_payload()},
                "artifacts": [{"type": "run_report", "provider": "playwright", "path": str(forged_report_path)}],
                "approvals": [],
                "verification": {"confidence": "high", "checks": [], "selected_provider": "playwright"},
            }

            report = verify_run_payload(payload)

            self.assertEqual(report["confidence"], "low")
            self.assertEqual(report["run_id_integrity"]["status"], "invalid")
            self.assertEqual(report["run_report"], None)
            self.assertEqual(report["run_report_path"], None)
            self.assertIn("invalid_run_id", [failure["type"] for failure in report["failures"]])
            self.assertIn("untrusted_artifact_path", [failure["type"] for failure in report["failures"]])
            self.assertNotIn("needle-dot-run-id-secret", json.dumps(report))

    def test_verify_payload_surfaces_policy_guard_for_blocked_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            report_path = _artifact_path(tmp, "run_blocked", "run-report.json")
            plan = {
                "primary_provider": "browser-use",
                "fallback_providers": [],
                "approval_required": False,
                "task": _task_payload(),
            }
            report_path.write_text(
                json.dumps(
                    {
                        "run_id": "run_blocked",
                        "plan_sha256": plan_fingerprint(plan),
                        "final_provider": "browser-use",
                        "final_status": "blocked",
                        "attempts": [
                            {
                                "order": 1,
                                "provider": "browser-use",
                                "status": "blocked",
                                "error": "Provider target URL resolved to a sensitive target scope",
                            }
                        ],
                        "cost_estimate": {"budget_status": "no_ceiling"},
                    }
                ),
                encoding="utf-8",
            )
            payload = {
                "run_id": "run_blocked",
                "status": "blocked",
                "plan": plan,
                "artifacts": [{"type": "run_report", "provider": "browser-use", "path": str(report_path)}],
                "events": [
                    {
                        "at": "2026-06-06T00:00:00+00:00",
                        "type": "blocked",
                        "reason": "provider_url_resolved_target_scope",
                        "provider": "browser-use",
                    }
                ],
                "approvals": [],
                "verification": {
                    "confidence": "high",
                    "checks": ["resolved provider target to sensitive scope was blocked"],
                    "selected_provider": "browser-use",
                },
            }
            report = verify_run_payload(payload)

            self.assertEqual(report["confidence"], "high")
            self.assertEqual(report["failures"], [])
            self.assertTrue(report["policy_guard"]["safety_stop"])
            self.assertTrue(report["policy_guard"]["non_resumable_safety_stop"])
            self.assertEqual(report["policy_guard"]["non_resumable_reason"], "provider_url_resolved_target_scope")
            self.assertEqual(report["policy_guard"]["target_scope"], "public_web")
            self.assertEqual(report["policy_guard"]["approval_status"], "not_required")
            self.assertEqual(report["policy_guard"]["safety_events"][0]["reason"], "provider_url_resolved_target_scope")
            self.assertEqual(report["policy_guard"]["blocked_reasons"][0]["source"], "event")
            self.assertEqual(report["policy_guard"]["blocked_reasons"][1]["source"], "attempt")
            self.assertEqual(report["plan_integrity"]["status"], "verified")
            self.assertIn("safety events surfaced: 1", report["checks"])
            self.assertIn("blocked reasons surfaced: 2", report["checks"])

    def test_verify_payload_reports_plan_fingerprint_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            report_path = _artifact_path(tmp, "run_plan_mismatch", "run-report.json")
            report_path.write_text(
                json.dumps(
                    {
                        "run_id": "run_plan_mismatch",
                        "plan_sha256": "0" * 64,
                        "final_provider": "playwright",
                        "final_status": "complete",
                        "attempts": [],
                        "cost_estimate": {"budget_status": "no_ceiling"},
                    }
                ),
                encoding="utf-8",
            )
            payload = {
                "run_id": "run_plan_mismatch",
                "status": "complete",
                "plan": {"primary_provider": "playwright", "fallback_providers": [], "task": _task_payload()},
                "artifacts": [{"type": "run_report", "provider": "playwright", "path": str(report_path)}],
                "approvals": [],
                "verification": {"confidence": "high", "checks": [], "selected_provider": "playwright"},
            }
            report = verify_run_payload(payload)

            self.assertEqual(report["confidence"], "low")
            self.assertEqual(report["plan_integrity"]["status"], "mismatch")
            self.assertEqual(report["failures"][0]["type"], "plan_fingerprint_mismatch")

    def test_verify_reports_complete_run_report_without_complete_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            plan = {"primary_provider": "playwright", "fallback_providers": [], "task": _task_payload()}
            report_path = _artifact_path(tmp, "run_false_complete", "run-report.json")
            report_path.write_text(
                json.dumps(
                    {
                        "run_id": "run_false_complete",
                        "plan_sha256": plan_fingerprint(plan),
                        "final_provider": "playwright",
                        "final_status": "complete",
                        "attempts": [
                            {
                                "order": 1,
                                "provider": "playwright",
                                "status": "failed",
                                "error": "simulated provider failure",
                            }
                        ],
                        "cost_estimate": {"budget_status": "no_ceiling"},
                    }
                ),
                encoding="utf-8",
            )
            payload = {
                "run_id": "run_false_complete",
                "status": "complete",
                "plan": plan,
                "artifacts": [{"type": "run_report", "provider": "playwright", "path": str(report_path)}],
                "approvals": [],
                "verification": {"confidence": "high", "checks": [], "selected_provider": "playwright"},
            }

            report = verify_run_payload(payload)

            self.assertEqual(report["confidence"], "low")
            self.assertEqual(report["failures"][0]["type"], "run_report_complete_without_complete_attempt")

    def test_verify_reports_run_report_run_id_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            plan = {"primary_provider": "playwright", "fallback_providers": [], "task": _task_payload()}
            report_path = _artifact_path(tmp, "run_report_mismatch", "run-report.json")
            report_path.write_text(
                json.dumps(
                    {
                        "run_id": "run_from_another_record",
                        "plan_sha256": plan_fingerprint(plan),
                        "final_provider": "playwright",
                        "final_status": "failed",
                        "attempts": [{"order": 1, "provider": "playwright", "status": "failed", "error": "simulated failure"}],
                        "cost_estimate": {"budget_status": "no_ceiling"},
                    }
                ),
                encoding="utf-8",
            )
            payload = {
                "run_id": "run_report_mismatch",
                "status": "failed",
                "plan": plan,
                "artifacts": [{"type": "run_report", "provider": "playwright", "path": str(report_path)}],
                "approvals": [],
                "verification": {"confidence": "medium", "checks": [], "selected_provider": "playwright"},
            }

            report = verify_run_payload(payload)

            self.assertEqual(report["confidence"], "low")
            self.assertIn("run_report_run_id_mismatch", [failure["type"] for failure in report["failures"]])
            mismatch = next(failure for failure in report["failures"] if failure["type"] == "run_report_run_id_mismatch")
            self.assertEqual(mismatch["run_id"], "run_report_mismatch")
            self.assertEqual(mismatch["run_report_run_id"], "run_from_another_record")

    def test_verify_uses_latest_run_report_when_multiple_reports_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            plan = {"primary_provider": "playwright", "fallback_providers": [], "task": _task_payload()}
            old_report_path = _artifact_path(tmp, "run_multiple_reports", "old-run-report.json")
            latest_report_path = _artifact_path(tmp, "run_multiple_reports", "run-report.json")
            old_report_path.write_text(
                json.dumps(
                    {
                        "run_id": "run_multiple_reports",
                        "plan_sha256": plan_fingerprint(plan),
                        "final_provider": "playwright",
                        "final_status": "failed",
                        "attempts": [{"order": 1, "provider": "playwright", "status": "failed", "error": "old failure"}],
                    }
                ),
                encoding="utf-8",
            )
            latest_report_path.write_text(
                json.dumps(
                    {
                        "run_id": "run_multiple_reports",
                        "plan_sha256": plan_fingerprint(plan),
                        "final_provider": "playwright",
                        "final_status": "complete",
                        "attempts": [{"order": 1, "provider": "playwright", "status": "complete"}],
                        "cost_estimate": {"budget_status": "no_ceiling"},
                    }
                ),
                encoding="utf-8",
            )
            payload = {
                "run_id": "run_multiple_reports",
                "status": "complete",
                "plan": plan,
                "artifacts": [
                    {"type": "run_report", "provider": "playwright", "path": str(old_report_path)},
                    {"type": "run_report", "provider": "playwright", "path": str(latest_report_path)},
                ],
                "approvals": [],
                "verification": {"confidence": "high", "checks": [], "selected_provider": "playwright"},
            }

            report = verify_run_payload(payload)

            self.assertEqual(report["confidence"], "high")
            self.assertEqual(report["failures"], [])
            self.assertEqual(report["run_report"]["final_status"], "complete")
            self.assertEqual(report["run_report_path"], str(latest_report_path))

    def test_verify_reports_final_provider_outside_planned_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            plan = {"primary_provider": "playwright", "fallback_providers": ["hyperbrowser"], "task": _task_payload()}
            report_path = _artifact_path(tmp, "run_wrong_provider", "run-report.json")
            report_path.write_text(
                json.dumps(
                    {
                        "run_id": "run_wrong_provider",
                        "plan_sha256": plan_fingerprint(plan),
                        "final_provider": "steel",
                        "final_status": "complete",
                        "attempts": [{"order": 1, "provider": "steel", "status": "complete"}],
                        "cost_estimate": {"budget_status": "no_ceiling"},
                    }
                ),
                encoding="utf-8",
            )
            payload = {
                "run_id": "run_wrong_provider",
                "status": "complete",
                "plan": plan,
                "artifacts": [{"type": "run_report", "provider": "steel", "path": str(report_path)}],
                "approvals": [],
                "verification": {"confidence": "high", "checks": [], "selected_provider": "steel"},
            }

            report = verify_run_payload(payload)

            self.assertEqual(report["confidence"], "low")
            self.assertEqual(report["failures"][0]["type"], "run_report_final_provider_not_planned")
            self.assertEqual(report["failures"][0]["planned_providers"], ["playwright", "hyperbrowser"])

    def test_verify_reports_approval_integrity_mismatch_after_approved_plan_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            approved = approve_run(run.run_id, approver="tester", reason="approved exact message")
            approved.plan["rationale"].append("tampered after approval")
            RunStore().save(approved)

            report = verify_run(run.run_id)

            self.assertEqual(report["confidence"], "low")
            self.assertEqual(report["approval_integrity"]["status"], "mismatch")
            self.assertFalse(report["approval_integrity"]["plan_matches"])
            self.assertTrue(report["approval_integrity"]["action_matches"])
            self.assertIn("approval integrity failed", report["checks"])
            self.assertEqual(report["failures"][0]["type"], "approval_integrity_mismatch")

    def test_verify_reports_missing_approval_id_after_approval_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            approved = approve_run(run.run_id, approver="tester", reason="approved exact message")
            approved.approvals[-1].pop("approval_id", None)
            RunStore().save(approved)

            report = verify_run(run.run_id)

            self.assertEqual(report["confidence"], "low")
            self.assertEqual(report["approval_integrity"]["status"], "missing_approval_id")
            self.assertFalse(report["approval_integrity"]["approval_id_present"])
            self.assertIn("approval integrity failed", report["checks"])
            self.assertEqual(report["failures"][0]["type"], "approval_integrity_missing_approval_id")

    def test_verify_reports_missing_approval_decision_metadata_after_approval_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            approved = approve_run(run.run_id, approver="tester", reason="approved exact message")
            approved.approvals[-1].pop("decided_at", None)
            approved.approvals[-1].pop("decided_by", None)
            RunStore().save(approved)

            report = verify_run(run.run_id)

            self.assertEqual(report["confidence"], "low")
            self.assertEqual(report["approval_integrity"]["status"], "missing_decision_metadata")
            self.assertFalse(report["approval_integrity"]["decision_metadata_present"])
            self.assertIn("approval integrity failed", report["checks"])
            self.assertEqual(report["failures"][0]["type"], "approval_integrity_missing_decision_metadata")

    def test_verify_reports_provider_allowlist_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", providers_allowed=["playwright"], execute=False)
            run.plan["primary_provider"] = "decodo-http"
            run.plan["fallback_providers"] = []
            RunStore().save(run)

            report = verify_run(run.run_id)

            self.assertEqual(report["confidence"], "low")
            self.assertIn("provider sequence constraints failed", report["checks"])
            self.assertEqual(report["failures"][0]["type"], "provider_allowlist_violation")
            self.assertEqual(report["failures"][0]["providers"], ["decodo-http"])
            self.assertEqual(report["failures"][0]["providers_allowed"], ["playwright"])

    def test_verify_reports_invalid_task_constraint_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            run.plan["task"]["max_cost_usd"] = math.nan
            RunStore().save(run)

            report = verify_run(run.run_id)

            self.assertEqual(report["confidence"], "low")
            self.assertIn("provider sequence constraints failed", report["checks"])
            self.assertEqual(report["failures"][0]["type"], "provider_constraint_invalid_task")

    def test_verify_reports_url_required_primary_missing_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Search the web for public mentions of this brand", execute=False)
            run.plan["primary_provider"] = "steel"
            run.plan["fallback_providers"] = []
            RunStore().save(run)

            report = verify_run(run.run_id)

            self.assertEqual(report["confidence"], "low")
            self.assertIn("provider sequence constraints failed", report["checks"])
            self.assertEqual(report["failures"][0]["type"], "provider_missing_url_constraint_violation")
            self.assertEqual(report["failures"][0]["provider"], "steel")

    def test_verify_reports_raw_http_without_http_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Search the web for public mentions of this brand", execute=False)
            run.plan["task"]["raw_http"] = True
            RunStore().save(run)

            report = verify_run(run.run_id)

            self.assertEqual(report["confidence"], "low")
            self.assertIn("provider sequence constraints failed", report["checks"])
            self.assertEqual(report["failures"][0]["type"], "provider_raw_http_url_constraint_violation")
            self.assertEqual(report["failures"][0]["allowed_schemes"], ["http", "https"])

    def test_verify_reports_fresh_retry_approval_required_after_external_write_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Post this comment on LinkedIn")
            failed_result = ExecutionResult(provider="browser-use", status="failed", error="simulated write failure")
            with patch("super_browser.runtime.execute_plan", return_value=failed_result):
                attempted = approve_run(run.run_id, approver="tester", reason="approved initial write", execute=True)

            report = verify_run(attempted.run_id)

            self.assertEqual(report["write_retry_guard"]["external_write_attempt_count"], 1)
            self.assertTrue(report["write_retry_guard"]["fresh_retry_approval_required"])
            self.assertFalse(report["write_retry_guard"]["pending_retry_approval"])
            self.assertFalse(report["write_retry_guard"]["retry_approval_after_last_attempt"])

    def test_verify_payload_marks_required_approval_record_missing(self):
        payload = {
            "run_id": "run_missing_approval",
            "status": "planned",
            "plan": {
                "primary_provider": "playwright",
                "fallback_providers": [],
                "approval_required": True,
                "task": _task_payload(
                    goal="Extract internal status",
                    url="http://10.0.0.5/status",
                    target_scope="private_network",
                ),
            },
            "artifacts": [],
            "events": [],
            "approvals": [],
            "verification": {"confidence": "medium", "checks": [], "selected_provider": "playwright"},
        }
        report = verify_run_payload(payload)

        self.assertEqual(report["policy_guard"]["approval_required"], True)
        self.assertEqual(report["policy_guard"]["approval_status"], "missing")
        self.assertEqual(report["failures"][0]["type"], "missing_approval_record")
        self.assertIn("approval record missing for approval-required plan", report["checks"])


if __name__ == "__main__":
    unittest.main()
