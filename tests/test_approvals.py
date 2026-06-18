import json
import os
import tempfile
import unittest
from unittest.mock import patch

from super_browser.mcp_server import handle_tool
from super_browser.models import ExecutionResult, action_fingerprint_from_plan, plan_fingerprint, utc_now
from super_browser.runtime import _plan_from_run, approve_run, create_run, deny_run, resume_run
from super_browser.store import RunStore
from super_browser.verifier import verify_run


class ApprovalTests(unittest.TestCase):
    def test_external_write_creates_pending_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Draft and post a LinkedIn comment")
            payload = run.to_dict()
            self.assertEqual(payload["status"], "awaiting_approval")
            self.assertEqual(len(payload["approvals"]), 1)
            self.assertEqual(payload["approvals"][0]["status"], "pending")
            self.assertEqual(payload["approvals"][0]["type"], "approval_request")
            self.assertIn("approval_id", payload["approvals"][0])
            self.assertIn("action_fingerprint", payload["approvals"][0])
            self.assertEqual(payload["approvals"][0]["plan_sha256"], plan_fingerprint(_plan_from_run(run)))

    def test_draft_only_social_task_does_not_create_pending_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Draft a LinkedIn comment, put it in the box, but do not publish", execute=False)
            payload = run.to_dict()
            self.assertEqual(payload["status"], "planned")
            self.assertEqual(payload["approvals"], [])
            self.assertTrue(payload["plan"]["task"]["draft_only"])

    def test_deny_run_records_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Submit this form to the public website")
            denied = deny_run(run.run_id, denied_by="tester", reason="not allowed")
            payload = denied.to_dict()
            self.assertEqual(payload["status"], "denied")
            self.assertEqual(payload["approvals"][0]["status"], "denied")
            self.assertEqual(payload["approvals"][0]["decided_by"], "tester")
            self.assertIn("approval was denied", payload["verification"]["checks"])

    def test_approve_and_deny_require_audit_actor_and_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            approve_target = create_run("Send a message in the browser")
            deny_target = create_run("Submit this form to the public website")

            with self.assertRaisesRegex(ValueError, "approval actor is required"):
                approve_run(approve_target.run_id, approver=" ", reason="approved")
            with self.assertRaisesRegex(ValueError, "denial actor is required"):
                deny_run(deny_target.run_id, denied_by="", reason="deny")
            with self.assertRaisesRegex(ValueError, "approval reason is required"):
                approve_run(approve_target.run_id, approver="tester")
            with self.assertRaisesRegex(ValueError, "denial reason is required"):
                deny_run(deny_target.run_id, denied_by="tester", reason=" ")

            stored_approve = RunStore().get(approve_target.run_id)
            stored_deny = RunStore().get(deny_target.run_id)
            self.assertEqual(stored_approve["status"], "awaiting_approval")
            self.assertEqual(stored_approve["approvals"][0]["status"], "pending")
            self.assertEqual(stored_deny["status"], "awaiting_approval")
            self.assertEqual(stored_deny["approvals"][0]["status"], "pending")

    def test_approve_rejects_tampered_pending_action_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Post this comment on LinkedIn")
            run.approvals[0]["action_fingerprint"] = "tampered"
            RunStore().save(run)

            with self.assertRaisesRegex(ValueError, "action fingerprint mismatch"):
                approve_run(run.run_id, approver="tester", reason="approved for test")

            stored = RunStore().get(run.run_id)
            self.assertEqual(stored["status"], "awaiting_approval")
            self.assertEqual(stored["approvals"][0]["status"], "pending")

    def test_approve_rejects_tampered_pending_plan_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            run.approvals[0]["plan_sha256"] = "tampered"
            RunStore().save(run)

            with self.assertRaisesRegex(ValueError, "plan fingerprint mismatch"):
                approve_run(run.run_id, approver="tester", reason="approved for test")

            stored = RunStore().get(run.run_id)
            self.assertEqual(stored["status"], "awaiting_approval")
            self.assertEqual(stored["approvals"][0]["status"], "pending")

    def test_approve_rejects_pending_approval_without_plan_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            del run.approvals[0]["plan_sha256"]
            RunStore().save(run)

            with self.assertRaisesRegex(ValueError, "plan fingerprint mismatch"):
                approve_run(run.run_id, approver="tester", reason="approved for test")

            stored = RunStore().get(run.run_id)
            self.assertEqual(stored["status"], "awaiting_approval")
            self.assertEqual(stored["approvals"][0]["status"], "pending")

    def test_approve_rejects_pending_approval_without_approval_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            del run.approvals[0]["approval_id"]
            RunStore().save(run)

            with self.assertRaisesRegex(ValueError, "approval id is missing"):
                approve_run(run.run_id, approver="tester", reason="approved for test")

            stored = RunStore().get(run.run_id)
            self.assertEqual(stored["status"], "awaiting_approval")
            self.assertEqual(stored["approvals"][0]["status"], "pending")

    def test_approve_run_records_decision_without_auto_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            approved = approve_run(run.run_id, approver="tester", reason="approved for test")
            payload = approved.to_dict()
            self.assertEqual(payload["status"], "approved")
            self.assertEqual(payload["approvals"][0]["status"], "approved")
            self.assertEqual(payload["approvals"][0]["decided_by"], "tester")
            self.assertNotIn("execution_started_after_approval", [event["type"] for event in payload["events"]])

    def test_approved_run_passes_approval_context_to_provider_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            complete_result = ExecutionResult(provider="browser-use", status="complete", verification={"confidence": "high", "checks": ["approved execution"]})
            with patch("super_browser.runtime.execute_plan", return_value=complete_result) as execute_mock:
                approved = approve_run(run.run_id, approver="tester", reason="approved for test", execute=True)
            self.assertEqual(approved.status, "complete")
            context = execute_mock.call_args.kwargs["approval_context"]
            self.assertEqual(context["approval_id"], run.approvals[0]["approval_id"])
            self.assertEqual(context["action_fingerprint"], action_fingerprint_from_plan(_plan_from_run(run)))
            self.assertEqual(context["plan_sha256"], plan_fingerprint(_plan_from_run(run)))
            self.assertEqual(context["plan_sha256"], run.approvals[0]["plan_sha256"])
            self.assertEqual(context["status"], "approved")

    def test_resume_rejects_expired_external_write_approval_before_execution_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            approved = approve_run(run.run_id, approver="tester", reason="approved for test")
            approved.approvals[-1]["decided_at"] = "2000-01-01T00:00:00+00:00"
            RunStore().save(approved)

            complete_result = ExecutionResult(provider="browser-use", status="complete", verification={"confidence": "low", "checks": ["should not execute"]})
            with patch("super_browser.runtime.execute_plan", return_value=complete_result) as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertFalse(execute_mock.called)
            self.assertEqual(payload["status"], "awaiting_approval")
            self.assertEqual(payload["approvals"][-2]["status"], "approved")
            self.assertEqual(payload["approvals"][-1]["status"], "pending")
            self.assertEqual(payload["approvals"][-1]["required_before"], "provider_execution")
            self.assertIn("approval_expired", [event["type"] for event in payload["events"]])
            self.assertEqual(payload["events"][-1]["reason"], "approval_expired")
            self.assertIn("fresh approval is required because the previous approval expired", payload["verification"]["checks"])

    def test_resume_rejects_plan_changed_after_approval_before_execution_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            approved = approve_run(run.run_id, approver="tester", reason="approved for test")
            approved.plan["rationale"].append("tampered after approval")
            RunStore().save(approved)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "approved")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "approval_integrity")
            self.assertEqual(block_event["approval_integrity_status"], "mismatch")
            self.assertIn("resume stopped because approval evidence no longer matches the current plan", payload["verification"]["checks"])
            self.assertEqual(payload["verification"]["approval_integrity"]["status"], "mismatch")
            stored = RunStore().get(run.run_id)
            self.assertEqual(stored["status"], "approved")
            self.assertIn("approval_integrity", [event.get("reason") for event in stored["events"]])

    def test_resume_rejects_approved_run_with_missing_approval_fingerprints_before_execution_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            approved = approve_run(run.run_id, approver="tester", reason="approved for test")
            approved.approvals[-1].pop("plan_sha256", None)
            approved.approvals[-1].pop("action_fingerprint", None)
            RunStore().save(approved)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "approved")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "approval_integrity")
            self.assertEqual(block_event["approval_integrity_status"], "missing_fingerprint")
            self.assertIn("resume stopped because approval evidence no longer matches the current plan", payload["verification"]["checks"])
            self.assertEqual(payload["verification"]["approval_integrity"]["status"], "missing_fingerprint")

    def test_resume_rejects_approved_run_with_missing_approval_record_before_execution_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            approved = approve_run(run.run_id, approver="tester", reason="approved for test")
            approved.approvals = []
            RunStore().save(approved)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "approved")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "approval_integrity")
            self.assertEqual(block_event["approval_integrity_status"], "missing")
            self.assertIn("resume stopped because approval evidence no longer matches the current plan", payload["verification"]["checks"])
            self.assertEqual(payload["verification"]["approval_integrity"]["status"], "missing")

    def test_resume_rejects_approved_run_with_missing_decision_metadata_before_execution_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            approved = approve_run(run.run_id, approver="tester", reason="approved for test")
            approved.approvals[-1].pop("decided_at", None)
            approved.approvals[-1].pop("decided_by", None)
            RunStore().save(approved)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "approved")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "approval_integrity")
            self.assertEqual(block_event["approval_integrity_status"], "missing_decision_metadata")
            self.assertIn("resume stopped because approval evidence no longer matches the current plan", payload["verification"]["checks"])
            self.assertEqual(payload["verification"]["approval_integrity"]["status"], "missing_decision_metadata")

    def test_resume_rejects_tampered_approval_flag_when_task_policy_requires_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser", execute=False)
            run.status = "planned"
            run.plan["approval_required"] = False
            run.approvals = []
            RunStore().save(run)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "planned")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "approval_integrity")
            self.assertEqual(block_event["approval_integrity_status"], "missing")
            self.assertEqual(payload["verification"]["approval_integrity"]["status"], "missing")

    def test_resume_rejects_tampered_target_scope_before_execution_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run(
                "Fetch this JSON endpoint through raw HTTP",
                url="http://169.254.169.254/latest/meta-data",
                execute=False,
            )
            run.status = "planned"
            run.plan["approval_required"] = False
            run.plan["task"]["target_scope"] = "public_web"
            run.approvals = []
            RunStore().save(run)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "planned")
            self.assertFalse(execute_mock.called)
            block_event = payload["events"][-1]
            self.assertEqual(block_event["type"], "resume_blocked")
            self.assertEqual(block_event["reason"], "provider_constraints")
            self.assertEqual(block_event["evidence_integrity_status"], "provider_target_scope_mismatch")
            failures = payload["verification"]["failures"]
            self.assertIn("provider_target_scope_mismatch", [failure["type"] for failure in failures])

    def test_resume_does_not_bypass_pending_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            resumed = resume_run(run.run_id)
            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "awaiting_approval")
            self.assertIn("resume_blocked", [event["type"] for event in payload["events"]])
            self.assertNotIn("execution_resumed", [event["type"] for event in payload["events"]])
            self.assertIn("approval is still pending", payload["verification"]["checks"][0])

    def test_link_local_target_requires_pending_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run(
                "Fetch this JSON endpoint through raw HTTP",
                url="http://169.254.169.254/latest/meta-data",
            )
            payload = run.to_dict()
            self.assertEqual(payload["status"], "awaiting_approval")
            self.assertEqual(payload["plan"]["task"]["target_scope"], "link_local")
            self.assertTrue(payload["plan"]["approval_required"])
            self.assertEqual(payload["approvals"][0]["risk"], "link_local")
            self.assertEqual(payload["approvals"][0]["target_url"], "http://169.254.169.254/latest/meta-data")
            self.assertEqual(payload["plan"]["council_report"]["approval_gate"]["reason"], "link-local target requires explicit approval")

            resumed = resume_run(run.run_id)
            self.assertEqual(resumed.status, "awaiting_approval")
            self.assertIn("resume_blocked", [event["type"] for event in resumed.events])

    def test_private_network_target_requires_pending_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run(
                "Fetch this JSON endpoint through raw HTTP",
                url="http://10.0.0.5/internal-status",
            )
            payload = run.to_dict()
            self.assertEqual(payload["status"], "awaiting_approval")
            self.assertEqual(payload["plan"]["task"]["target_scope"], "private_network")
            self.assertTrue(payload["plan"]["approval_required"])
            self.assertEqual(payload["approvals"][0]["risk"], "private_network")
            self.assertEqual(payload["approvals"][0]["target_url"], "http://10.0.0.5/internal-status")
            self.assertEqual(payload["plan"]["council_report"]["approval_gate"]["reason"], "private-network target requires explicit approval")

            resumed = resume_run(run.run_id)
            self.assertEqual(resumed.status, "awaiting_approval")
            self.assertIn("resume_blocked", [event["type"] for event in resumed.events])

    def test_local_file_target_requires_pending_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            fixture_path = os.path.join(tmp, "local-fixture.html")
            with open(fixture_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>fixture</body></html>")

            run = create_run(
                "Extract this local browser fixture",
                url=f"file://{fixture_path}",
            )
            payload = run.to_dict()
            self.assertEqual(payload["status"], "awaiting_approval")
            self.assertEqual(payload["plan"]["task"]["target_scope"], "local_file")
            self.assertTrue(payload["plan"]["approval_required"])
            self.assertEqual(payload["approvals"][0]["risk"], "local_file")
            self.assertEqual(payload["approvals"][0]["target_url"], f"file://{fixture_path}")
            self.assertEqual(payload["plan"]["council_report"]["approval_gate"]["reason"], "local file target requires explicit approval")

            resumed = resume_run(run.run_id)
            self.assertEqual(resumed.status, "awaiting_approval")
            self.assertIn("resume_blocked", [event["type"] for event in resumed.events])

    def test_embedded_local_file_url_in_goal_requires_pending_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            fixture_path = os.path.join(tmp, "local-fixture.html")
            fixture_url = f"file://{fixture_path}"
            with open(fixture_path, "w", encoding="utf-8") as handle:
                handle.write("<html><body>fixture</body></html>")

            run = create_run(f"Extract this local browser fixture from {fixture_url}")
            payload = run.to_dict()

            self.assertEqual(payload["status"], "awaiting_approval")
            self.assertEqual(payload["plan"]["task"]["url"], fixture_url)
            self.assertEqual(payload["plan"]["task"]["target_scope"], "local_file")
            self.assertTrue(payload["plan"]["approval_required"])
            self.assertEqual(payload["approvals"][0]["risk"], "local_file")
            self.assertEqual(payload["approvals"][0]["target_url"], fixture_url)
            self.assertEqual(payload["plan"]["primary_provider"], "playwright")
            self.assertEqual(payload["plan"]["fallback_providers"], [])

    def test_mcp_approval_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Publish a social media post")
            payload = handle_tool("approve_browser_run", {"run_id": run.run_id, "by": "mcp-test", "reason": "fixture"})
            self.assertEqual(payload["status"], "approved")
            self.assertEqual(payload["approvals"][0]["decided_by"], "mcp-test")

    def test_external_write_resume_requires_fresh_approval_after_started_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Post this comment on LinkedIn")
            failed_result = ExecutionResult(provider="browser-use", status="failed", error="simulated write failure")
            with patch("super_browser.runtime.execute_plan", return_value=failed_result):
                attempted = approve_run(run.run_id, approver="tester", reason="approved initial write", execute=True)
            self.assertEqual(attempted.status, "failed")
            self.assertEqual(len([event for event in attempted.events if event["type"] == "external_write_attempt_started"]), 1)

            resumed = resume_run(run.run_id)
            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "awaiting_approval")
            self.assertEqual(payload["approvals"][-1]["status"], "pending")
            self.assertEqual(payload["approvals"][-1]["required_before"], "provider_retry")
            self.assertIn("fresh approval is required", payload["verification"]["checks"][1])
            self.assertEqual(len([event for event in payload["events"] if event["type"] == "external_write_attempt_started"]), 1)
            self.assertIn("external_write_retry_blocked", [event["type"] for event in payload["events"]])
            report = verify_run(run.run_id)
            self.assertTrue(report["write_retry_guard"]["pending_retry_approval"])
            self.assertEqual(report["write_retry_guard"]["external_write_attempt_count"], 1)
            self.assertIn("external write retry is blocked pending fresh approval", report["checks"])

    def test_runtime_exception_after_approved_external_write_requires_fresh_retry_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Post this comment on LinkedIn")

            with patch("super_browser.runtime.execute_plan", side_effect=RuntimeError("BROWSER_USE_API_KEY=runtime-secret")):
                attempted = approve_run(run.run_id, approver="tester", reason="approved initial write", execute=True)

            attempted_payload = attempted.to_dict()
            report = verify_run(run.run_id)
            serialized = json.dumps({"payload": attempted_payload, "report": report})

            self.assertEqual(attempted_payload["status"], "failed")
            self.assertEqual(attempted_payload["execution_lease"], {})
            self.assertEqual(len([event for event in attempted_payload["events"] if event["type"] == "external_write_attempt_started"]), 1)
            self.assertIn("execution_exception", [event["type"] for event in attempted_payload["events"]])
            self.assertIn("runtime execution exception was captured", report["checks"])
            self.assertTrue(report["write_retry_guard"]["fresh_retry_approval_required"])
            self.assertNotIn("runtime-secret", serialized)
            self.assertIn("BROWSER_USE_API_KEY=[REDACTED]", serialized)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)
            payload = resumed.to_dict()
            self.assertFalse(execute_mock.called)
            self.assertEqual(payload["status"], "awaiting_approval")
            self.assertEqual(payload["approvals"][-1]["status"], "pending")
            self.assertEqual(payload["approvals"][-1]["required_before"], "provider_retry")
            self.assertIn("external_write_retry_blocked", [event["type"] for event in payload["events"]])
            self.assertEqual(len([event for event in payload["events"] if event["type"] == "external_write_attempt_started"]), 1)

    def test_external_write_retry_guard_uses_policy_when_stored_flag_is_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Post this comment on LinkedIn")
            run.plan["task"]["external_write"] = False
            tampered_plan = _plan_from_run(run)
            run.approvals[0]["action_fingerprint"] = action_fingerprint_from_plan(tampered_plan)
            run.approvals[0]["plan_sha256"] = plan_fingerprint(tampered_plan)
            RunStore().save(run)

            failed_result = ExecutionResult(provider="browser-use", status="failed", error="simulated write failure")
            with patch("super_browser.runtime.execute_plan", return_value=failed_result):
                attempted = approve_run(run.run_id, approver="tester", reason="approved initial write", execute=True)
            attempted_payload = attempted.to_dict()
            self.assertEqual(attempted_payload["status"], "failed")
            self.assertFalse(attempted_payload["plan"]["task"]["external_write"])
            self.assertEqual(len([event for event in attempted_payload["events"] if event["type"] == "external_write_attempt_started"]), 1)

            report = verify_run(run.run_id)
            self.assertTrue(report["policy_guard"]["external_write"])
            self.assertTrue(report["write_retry_guard"]["fresh_retry_approval_required"])
            self.assertEqual(report["write_retry_guard"]["external_write_attempt_count"], 1)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)
            payload = resumed.to_dict()
            self.assertFalse(execute_mock.called)
            self.assertEqual(payload["status"], "awaiting_approval")
            self.assertEqual(payload["approvals"][-1]["status"], "pending")
            self.assertEqual(payload["approvals"][-1]["required_before"], "provider_retry")
            self.assertIn("external_write_retry_blocked", [event["type"] for event in payload["events"]])

    def test_credential_external_write_resume_requires_fresh_retry_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Use my logged in Chrome session to post a LinkedIn comment")
            self.assertTrue(run.plan["task"]["requires_auth"])
            self.assertTrue(run.plan["task"]["external_write"])

            failed_result = ExecutionResult(provider="browser-use", status="failed", error="simulated logged-in post failure")
            with patch("super_browser.runtime.execute_plan", return_value=failed_result):
                attempted = approve_run(run.run_id, approver="tester", reason="approved initial logged-in post", execute=True)
            self.assertEqual(attempted.status, "failed")
            self.assertEqual(len([event for event in attempted.events if event["type"] == "external_write_attempt_started"]), 1)

            resumed = resume_run(run.run_id)
            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "awaiting_approval")
            self.assertEqual(payload["approvals"][-1]["required_before"], "provider_retry")
            self.assertEqual(len([event for event in payload["events"] if event["type"] == "external_write_attempt_started"]), 1)
            self.assertIn("external_write_retry_blocked", [event["type"] for event in payload["events"]])

    def test_stale_external_write_execution_recovers_to_retry_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Post this comment on LinkedIn")
            approved = approve_run(run.run_id, approver="tester", reason="approved initial write")
            plan = _plan_from_run(approved)
            fingerprint = action_fingerprint_from_plan(plan)
            store = RunStore()
            store.claim_execution(
                approved.run_id,
                "approved",
                [
                    {"at": utc_now(), "type": "execution_started_after_approval", "provider": plan.primary_provider},
                    {
                        "at": utc_now(),
                        "type": "external_write_attempt_started",
                        "provider": plan.primary_provider,
                        "action_fingerprint": fingerprint,
                        "approval_id": approved.approvals[0]["approval_id"],
                        "approved_by": "tester",
                    },
                ],
                lease_seconds=0,
            )

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            payload = resumed.to_dict()
            self.assertFalse(execute_mock.called)
            self.assertEqual(payload["status"], "awaiting_approval")
            self.assertEqual(payload["approvals"][-1]["status"], "pending")
            self.assertEqual(payload["approvals"][-1]["required_before"], "provider_retry")
            self.assertIn("stale_execution_recovered", [event["type"] for event in payload["events"]])
            self.assertIn("external_write_retry_blocked", [event["type"] for event in payload["events"]])

    def test_fresh_retry_approval_allows_one_external_write_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Post this comment on LinkedIn")
            failed_result = ExecutionResult(provider="browser-use", status="failed", error="simulated write failure")
            complete_result = ExecutionResult(provider="browser-use", status="complete", verification={"confidence": "medium", "checks": ["retry completed"]})
            with patch("super_browser.runtime.execute_plan", return_value=failed_result):
                approve_run(run.run_id, approver="tester", reason="approved initial write", execute=True)
            retry_gate = resume_run(run.run_id)

            with patch("super_browser.runtime.execute_plan", return_value=complete_result):
                retried = approve_run(retry_gate.run_id, approver="tester", reason="approved one retry", execute=True)
            payload = retried.to_dict()
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(payload["approvals"][-1]["status"], "approved")
            self.assertEqual(payload["approvals"][-1]["required_before"], "provider_retry")
            self.assertEqual(len([event for event in payload["events"] if event["type"] == "external_write_attempt_started"]), 2)


if __name__ == "__main__":
    unittest.main()
