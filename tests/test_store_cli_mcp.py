import io
import json
import math
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from super_browser.cli import main as cli_main
from super_browser.live_evidence import load_live_test_evidence, record_live_test_evidence
from super_browser import mcp_server, setup_helpers
from super_browser.mcp_server import TOOLS, handle_tool, list_resources, main as mcp_main, read_resource
from super_browser.models import ExecutionResult, RunState, approval_request_from_plan, plan_fingerprint, utc_now
from super_browser.production import production_readiness
from super_browser.runtime import approve_run, create_run, resume_run
from super_browser.router import build_plan, infer_task
from super_browser.store import RunStore


class _JsonHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok": true, "name": "mcp-fixture"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def _write_minimal_super_browser_root(root):
    os.makedirs(root, exist_ok=True)
    os.makedirs(os.path.join(root, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(root, "mcp"), exist_ok=True)
    os.makedirs(os.path.join(root, "references"), exist_ok=True)
    os.makedirs(os.path.join(root, "skills", "minimal-specialist"), exist_ok=True)
    with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as handle:
        handle.write("# Super Saiyan Browser\n")
    with open(os.path.join(root, "SKILL.md"), "w", encoding="utf-8") as handle:
        handle.write("# Super Saiyan Browser Skill\n")
    with open(os.path.join(root, "references", "provider-matrix.md"), "w", encoding="utf-8") as handle:
        handle.write("# Provider Matrix\n")
    with open(os.path.join(root, "skills", "minimal-specialist", "SKILL.md"), "w", encoding="utf-8") as handle:
        handle.write("# Minimal Specialist\n")
    for relative in ["scripts/super-browser", "mcp/super-browser-server"]:
        path = os.path.join(root, relative)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("#!/usr/bin/env bash\n")
        os.chmod(path, 0o755)


def _artifact_path(state_dir: str, run_id: str, filename: str) -> str:
    path = os.path.join(state_dir, "artifacts", run_id, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _corrupt_stored_run_payload(state_dir: str, run_id: str, payload: str = "{not-json") -> None:
    conn = sqlite3.connect(os.path.join(state_dir, "runs.sqlite"))
    try:
        conn.execute("UPDATE runs SET payload = ? WHERE run_id = ?", (payload, run_id))
        conn.commit()
    finally:
        conn.close()


class StoreCliMcpTests(unittest.TestCase):
    def test_store_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            store = RunStore()
            run = RunState.create(build_plan(infer_task("Extract a page")), status="planned")
            store.save(run)
            loaded = store.get(run.run_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["run_id"], run.run_id)

    def test_read_only_store_does_not_create_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = os.path.join(tmp, "missing-state")
            os.environ["SUPER_BROWSER_STATE_DIR"] = state_dir
            store = RunStore(create=False)
            self.assertEqual(store.list(), [])
            self.assertIsNone(store.get("run_missing"))
            self.assertFalse(os.path.exists(state_dir))

    def test_store_surfaces_corrupt_run_payload_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            _corrupt_stored_run_payload(tmp, run.run_id)

            loaded = RunStore(create=False).get(run.run_id)
            self.assertEqual(loaded["run_id"], run.run_id)
            self.assertEqual(loaded["status"], "failed")
            self.assertEqual(loaded["plan"], {})
            self.assertEqual(loaded["store_error"]["type"], "store_payload_corrupt")
            self.assertEqual(loaded["store_error"]["stored_status"], "planned")
            self.assertIn("stored run payload could not be decoded", loaded["verification"]["checks"])

            listed = {item["run_id"]: item for item in RunStore(create=False).list(include_details=False)}
            self.assertEqual(listed[run.run_id]["status"], "failed")
            self.assertEqual(listed[run.run_id]["confidence"], "low")
            self.assertEqual(listed[run.run_id]["store_error"]["type"], "store_payload_corrupt")

    def test_cli_and_mcp_get_surface_corrupt_run_payload_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            _corrupt_stored_run_payload(tmp, run.run_id)

            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["get", run.run_id])
            self.assertEqual(code, 0)
            cli_payload = json.loads(output.getvalue())
            self.assertEqual(cli_payload["store_error"]["type"], "store_payload_corrupt")

            mcp_payload = handle_tool("get_browser_run", {"run_id": run.run_id})
            self.assertEqual(mcp_payload["store_error"]["type"], "store_payload_corrupt")
            self.assertNotIn("not-json", json.dumps(cli_payload))
            self.assertNotIn("not-json", json.dumps(mcp_payload))

    def test_resume_corrupt_run_payload_blocks_before_provider_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            _corrupt_stored_run_payload(tmp, run.run_id)

            with patch("super_browser.runtime.execute_plan") as execute_mock:
                resumed = resume_run(run.run_id)

            execute_mock.assert_not_called()
            payload = resumed.to_dict()
            self.assertEqual(payload["status"], "failed")
            self.assertIn("resume_blocked", [event["type"] for event in payload["events"]])
            self.assertIn("provider_constraints", [event.get("reason") for event in payload["events"]])
            failure_types = [failure["type"] for failure in payload["verification"]["failures"]]
            self.assertIn("store_payload_corrupt", failure_types)
            self.assertIn("provider_constraint_invalid_task", failure_types)

    def test_cli_plan_outputs_json(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(["plan", "--goal", "Extract titles from https://example.com"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["primary_provider"], "playwright")

    def test_cli_plan_rejects_raw_http_without_endpoint(self):
        output = io.StringIO()
        error_output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error_output):
            code = cli_main(["plan", "--goal", "Fetch this JSON endpoint through raw HTTP"])

        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("Raw HTTP/API tasks require an http(s) starting URL", error_output.getvalue())

    def test_cli_known_command_exceptions_are_structured_and_redacted(self):
        output = io.StringIO()
        error_output = io.StringIO()
        with patch(
            "super_browser.cli.resume_run",
            side_effect=RuntimeError("BROWSER_USE_API_KEY=super-secret-value"),
        ):
            with redirect_stdout(output), redirect_stderr(error_output):
                code = cli_main(["resume", "run_any"])

        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), "")
        payload = json.loads(error_output.getvalue())
        self.assertEqual(payload["error_type"], "RuntimeError")
        self.assertIn("BROWSER_USE_API_KEY=[REDACTED]", payload["error"])
        self.assertNotIn("super-secret-value", error_output.getvalue())

    def test_mcp_plan_rejects_raw_http_without_endpoint(self):
        with self.assertRaisesRegex(ValueError, "Raw HTTP/API tasks require an http\\(s\\) starting URL"):
            handle_tool("plan_browser_task", {"goal": "Fetch this JSON endpoint through raw HTTP"})

    def test_cli_plan_honors_allow_provider_and_cost(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                [
                    "plan",
                    "--goal",
                    "Fetch this JSON endpoint through raw HTTP",
                    "--url",
                    "https://example.com/data.json",
                    "--allow-provider",
                    "decodo-http",
                    "--max-cost-usd",
                    "0.01",
                    "--timeout-seconds",
                    "45",
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["primary_provider"], "decodo-http")
        self.assertEqual(payload["fallback_providers"], [])
        self.assertEqual(payload["task"]["providers_allowed"], ["decodo-http"])
        self.assertEqual(payload["task"]["max_cost_usd"], 0.01)
        self.assertEqual(payload["task"]["timeout_seconds"], 45)
        self.assertEqual(payload["council_report"]["planner_decision"]["timeout_seconds"], 45)

    def test_cli_run_approval_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["run", "--goal", "Post this comment on LinkedIn"])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "awaiting_approval")

    def test_cli_run_plan_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["run", "--goal", "Extract titles from https://example.com", "--plan-only", "--timeout-seconds", "20"])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "planned")
            self.assertEqual([artifact["type"] for artifact in payload["artifacts"]], ["plan"])
            self.assertEqual(payload["plan"]["task"]["timeout_seconds"], 20)

    def test_cli_production_readiness_blocks_when_live_providers_unproven(self):
        old_env = {name: os.environ.get(name) for name in [
            "BROWSER_USE_API_KEY",
            "ORGO_API_KEY",
            "ORGO_COMPUTER_ID",
            "AIRTOP_API_KEY",
            "HYPERBROWSER_API_KEY",
            "STEEL_API_KEY",
            "DECODO_PROXY",
            "BRIGHTDATA_API_KEY",
            "BRIGHTDATA_UNLOCKER_ZONE",
            "BRIGHTDATA_SERP_ZONE",
            "BRIGHTDATA_BROWSER_ZONE",
            "BRIGHTDATA_BROWSER_USERNAME",
            "BRIGHTDATA_BROWSER_PASSWORD",
            "BRIGHTDATA_CUSTOMER_ID",
        ]}
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            for name in old_env:
                os.environ.pop(name, None)
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                output = io.StringIO()
                with patch("super_browser.cli.load_env_file"), redirect_stdout(output):
                    code = cli_main(["production-readiness"])

            self.assertEqual(code, 1)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["production_ready"])
            self.assertEqual(payload["status"], "blocked")
            self.assertIn("browser-use", payload["blocked_providers"])
            self.assertIn("BROWSER_USE_API_KEY", payload["missing_env"])
            self.assertIn("decodo-http", payload["uncertified_providers"])
            self.assertTrue(any("live-test" in action for action in payload["next_actions"]))
        finally:
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_cli_env_checklist_reports_setup_without_secret_values(self):
        env_names = [
            "BROWSER_USE_API_KEY",
            "ORGO_API_KEY",
            "ORGO_COMPUTER_ID",
            "AIRTOP_API_KEY",
            "HYPERBROWSER_API_KEY",
            "STEEL_API_KEY",
            "DECODO_PROXY",
            "BRIGHTDATA_API_KEY",
            "BRIGHTDATA_UNLOCKER_ZONE",
            "BRIGHTDATA_SERP_ZONE",
            "BRIGHTDATA_BROWSER_ZONE",
            "BRIGHTDATA_BROWSER_USERNAME",
            "BRIGHTDATA_BROWSER_PASSWORD",
            "BRIGHTDATA_CUSTOMER_ID",
        ]
        old_env = {name: os.environ.get(name) for name in env_names}
        try:
            for name in env_names:
                os.environ.pop(name, None)
            os.environ["BROWSER_USE_API_KEY"] = "super-secret-browser-use-key"
            os.environ["DECODO_PROXY"] = "http://user:super-secret-proxy-pass@proxy.example:8080"
            output = io.StringIO()
            with patch("super_browser.cli.load_env_file"), redirect_stdout(output):
                code = cli_main(["env-checklist"])

            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            rendered = json.dumps(payload)
            self.assertEqual(payload["type"], "super_browser_env_checklist")
            self.assertFalse(payload["values_included"])
            self.assertIn("HYPERBROWSER_API_KEY", payload["missing_required_env"])
            self.assertNotIn("super-secret-browser-use-key", rendered)
            self.assertNotIn("super-secret-proxy-pass", rendered)
            browser_use = [item for item in payload["providers"] if item["name"] == "browser-use"][0]
            browser_use_key = [item for item in browser_use["required_env"] if item["name"] == "BROWSER_USE_API_KEY"][0]
            self.assertTrue(browser_use_key["configured"])
            self.assertTrue(browser_use_key["sensitive"])
            decodo = [item for item in payload["providers"] if item["name"] == "decodo-http"][0]
            decodo_proxy = [item for item in decodo["optional_env"] if item["name"] == "DECODO_PROXY"][0]
            self.assertTrue(decodo_proxy["configured"])
            global_names = {item["name"] for item in payload["global_env"]}
            self.assertIn("SUPER_BROWSER_ALLOW_INTERNAL_PROVIDER_BASES", global_names)
            self.assertIn("SUPER_BROWSER_ALLOW_INSECURE_PROVIDER_BASES", global_names)
            self.assertIn("SUPER_BROWSER_APPROVAL_TTL_SECONDS", global_names)
            self.assertTrue(any("production-readiness" in command for command in payload["commands"]))
            self.assertTrue(any("super-browser setup" in command for command in payload["commands"]))
            browser_use_signup = next(
                item for item in payload["provider_signup"] if item["env_var"] == "BROWSER_USE_API_KEY"
            )
            self.assertIn("signup_url", browser_use_signup)
        finally:
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_cli_setup_returns_walkthrough(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(["setup", "--client", "cursor"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["type"], "super_browser_setup_walkthrough")
        self.assertEqual(len(payload["steps"]), 13)
        self.assertTrue(any("init-mcp" in command for step in payload["steps"] for command in step.get("commands", [])))
        self.assertTrue(any(step.get("title") == "Optional: Chrome extension" for step in payload["steps"]))
        self.assertTrue(any(step.get("title") == "Bright Data zones (optional)" for step in payload["steps"]))

    def test_cli_install_skill_copies_self_contained_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "skills")
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["install-skill", "--target", target])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            installed_path = payload["installed_path"]
            self.assertEqual(payload["status"], "installed")
            self.assertTrue(os.path.exists(os.path.join(installed_path, "SKILL.md")))
            self.assertTrue(os.path.exists(os.path.join(installed_path, "skills", "super-browser-orchestrator", "SKILL.md")))
            self.assertTrue(os.path.exists(os.path.join(installed_path, "references", "routing-playbook.md")))
            self.assertTrue(os.path.exists(os.path.join(installed_path, "mcp", "super-browser-server")))
            self.assertEqual(payload["mcp_config"]["mcpServers"]["super-browser"]["cwd"], installed_path)
            stale_path = os.path.join(installed_path, "stale-file.txt")
            with open(stale_path, "w", encoding="utf-8") as handle:
                handle.write("old install artifact")

            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["install-skill", "--target", target, "--force"])
            self.assertEqual(code, 0)
            self.assertFalse(os.path.exists(stale_path))

    def test_cli_install_skill_writes_hashed_bundle_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "skills")
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["install-skill", "--target", target])

            self.assertEqual(code, 0)
            installed_path = json.loads(output.getvalue())["installed_path"]
            manifest_path = os.path.join(installed_path, "super-browser-manifest.json")
            self.assertTrue(os.path.exists(manifest_path))

            with open(manifest_path, encoding="utf-8") as handle:
                manifest = json.load(handle)
            self.assertEqual(manifest["type"], "super_browser_bundle_manifest")
            self.assertEqual(manifest["status"], "ok")
            self.assertEqual(manifest["root"], installed_path)
            self.assertTrue(manifest["entrypoints"]["cli"]["present"])
            self.assertTrue(manifest["entrypoints"]["mcp_server"]["present"])
            self.assertIn("plan_browser_task", manifest["mcp_tools"])
            self.assertIn("production_readiness", manifest["mcp_tools"])
            self.assertIn("bundle_manifest", manifest["mcp_tools"])
            relative_files = {item["path"] for item in manifest["files"]}
            self.assertIn("SKILL.md", relative_files)
            self.assertIn("skills/super-browser-orchestrator/SKILL.md", relative_files)
            self.assertIn("references/routing-playbook.md", relative_files)
            self.assertNotIn(".env", relative_files)
            self.assertNotIn(".super-browser/runs.sqlite", relative_files)
            skill_entry = [item for item in manifest["files"] if item["path"] == "SKILL.md"][0]
            self.assertRegex(skill_entry["sha256"], r"^[0-9a-f]{64}$")

    def test_cli_bundle_manifest_reports_agent_handoff_inventory(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(["bundle-manifest"])

        self.assertEqual(code, 0)
        manifest = json.loads(output.getvalue())
        self.assertEqual(manifest["type"], "super_browser_bundle_manifest")
        self.assertEqual(manifest["status"], "ok")
        self.assertGreater(manifest["file_count"], 20)
        self.assertIn("playwright", manifest["providers"])
        self.assertIn("browser-use", manifest["providers"])
        self.assertIn("hyperbrowser", manifest["providers"])
        self.assertIn("super-browser-orchestrator", manifest["skills"])
        self.assertTrue(manifest["required_paths"]["README.md"]["present"])
        self.assertTrue(manifest["required_paths"]["mcp/super-browser-server"]["executable"])
        self.assertIn("super-browser://references/provider-matrix", manifest["resources"])
        self.assertIn("super-browser://skills/super-browser-orchestrator", manifest["resources"])
        self.assertEqual(manifest["mcp_tools"], [tool["name"] for tool in TOOLS])
        self.assertIn("production_readiness", manifest["mcp_tools"])
        self.assertIn("bundle_manifest", manifest["mcp_tools"])

    def test_cli_install_skill_rejects_unsafe_bundle_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            error_output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(error_output):
                code = cli_main(["install-skill", "--target", tmp, "--name", "..", "--force"])
            self.assertEqual(code, 1)
            self.assertIn("skill bundle name must be a simple directory name", error_output.getvalue())
            self.assertFalse(os.path.exists(os.path.join(tmp, "SKILL.md")))

    def test_cli_install_skill_excludes_local_secrets_state_and_caches(self):
        old_root = os.environ.get("SUPER_BROWSER_REPO_ROOT")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fake_source = os.path.join(tmp, "source-repo")
                target = os.path.join(tmp, "skills")
                _write_minimal_super_browser_root(fake_source)
                os.makedirs(os.path.join(fake_source, ".venv"))
                os.makedirs(os.path.join(fake_source, "node_modules"))
                os.makedirs(os.path.join(fake_source, ".super-browser"))
                os.makedirs(os.path.join(fake_source, "__pycache__"))
                for filename in [".env", ".env.local", "debug.log", "runs.sqlite", "cache.db", ".DS_Store"]:
                    with open(os.path.join(fake_source, filename), "w", encoding="utf-8") as handle:
                        handle.write("local-only")
                with open(os.path.join(fake_source, ".venv", "secret.txt"), "w", encoding="utf-8") as handle:
                    handle.write("do not copy")
                with open(os.path.join(fake_source, "node_modules", "package.txt"), "w", encoding="utf-8") as handle:
                    handle.write("do not copy")
                with open(os.path.join(fake_source, ".super-browser", "runs.sqlite"), "w", encoding="utf-8") as handle:
                    handle.write("do not copy")

                os.environ["SUPER_BROWSER_REPO_ROOT"] = fake_source
                output = io.StringIO()
                with redirect_stdout(output):
                    code = cli_main(["install-skill", "--target", target])

                self.assertEqual(code, 0)
                installed_path = json.loads(output.getvalue())["installed_path"]
                self.assertTrue(os.path.exists(os.path.join(installed_path, "SKILL.md")))
                for relative in [
                    ".env",
                    ".env.local",
                    "debug.log",
                    "runs.sqlite",
                    "cache.db",
                    ".DS_Store",
                    ".venv",
                    "node_modules",
                    ".super-browser",
                    "__pycache__",
                ]:
                    self.assertFalse(os.path.exists(os.path.join(installed_path, relative)), relative)
        finally:
            if old_root is None:
                os.environ.pop("SUPER_BROWSER_REPO_ROOT", None)
            else:
                os.environ["SUPER_BROWSER_REPO_ROOT"] = old_root

    def test_cli_install_skill_does_not_follow_source_symlinks(self):
        old_root = os.environ.get("SUPER_BROWSER_REPO_ROOT")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fake_source = os.path.join(tmp, "source-repo")
                target = os.path.join(tmp, "skills")
                external_secret = os.path.join(tmp, "outside-secret.txt")
                external_dir = os.path.join(tmp, "outside-dir")
                _write_minimal_super_browser_root(fake_source)
                os.makedirs(external_dir)
                with open(external_secret, "w", encoding="utf-8") as handle:
                    handle.write("outside secret")
                with open(os.path.join(external_dir, "secret.txt"), "w", encoding="utf-8") as handle:
                    handle.write("outside dir secret")
                try:
                    os.symlink(external_secret, os.path.join(fake_source, "linked-secret.txt"))
                    os.symlink(external_dir, os.path.join(fake_source, "linked-dir"))
                except (AttributeError, NotImplementedError, OSError) as exc:
                    self.skipTest(f"symlink creation unavailable: {exc}")

                os.environ["SUPER_BROWSER_REPO_ROOT"] = fake_source
                output = io.StringIO()
                with redirect_stdout(output):
                    code = cli_main(["install-skill", "--target", target])

                self.assertEqual(code, 0)
                installed_path = json.loads(output.getvalue())["installed_path"]
                self.assertTrue(os.path.exists(os.path.join(installed_path, "SKILL.md")))
                self.assertFalse(os.path.lexists(os.path.join(installed_path, "linked-secret.txt")))
                self.assertFalse(os.path.lexists(os.path.join(installed_path, "linked-dir")))
        finally:
            if old_root is None:
                os.environ.pop("SUPER_BROWSER_REPO_ROOT", None)
            else:
                os.environ["SUPER_BROWSER_REPO_ROOT"] = old_root

    def test_cli_install_skill_rejects_destination_that_contains_source(self):
        old_root = os.environ.get("SUPER_BROWSER_REPO_ROOT")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                destination = os.path.join(tmp, "installed")
                fake_source = os.path.join(destination, "source-repo")
                _write_minimal_super_browser_root(fake_source)

                os.environ["SUPER_BROWSER_REPO_ROOT"] = fake_source
                output = io.StringIO()
                error_output = io.StringIO()
                with redirect_stdout(output), redirect_stderr(error_output):
                    code = cli_main(["install-skill", "--target", tmp, "--name", "installed", "--force"])

                self.assertEqual(code, 1)
                self.assertIn("install destination must not contain the Super Saiyan Browser repository", error_output.getvalue())
                self.assertTrue(os.path.exists(os.path.join(fake_source, "SKILL.md")))
        finally:
            if old_root is None:
                os.environ.pop("SUPER_BROWSER_REPO_ROOT", None)
            else:
                os.environ["SUPER_BROWSER_REPO_ROOT"] = old_root

    def test_package_only_install_skill_does_not_copy_python_library_tree(self):
        old_root = os.environ.get("SUPER_BROWSER_REPO_ROOT")
        try:
            os.environ.pop("SUPER_BROWSER_REPO_ROOT", None)
            with tempfile.TemporaryDirectory() as tmp:
                venv = os.path.join(tmp, "venv")
                package_file = os.path.join(venv, "lib", "python3.14", "site-packages", "super_browser", "setup_helpers.py")
                os.makedirs(os.path.dirname(package_file))
                with open(package_file, "w", encoding="utf-8") as handle:
                    handle.write("# installed package placeholder\n")
                target = os.path.join(tmp, "skills")

                with patch.object(setup_helpers, "__file__", package_file), patch.object(setup_helpers.sys, "prefix", venv), patch.object(
                    setup_helpers.sys, "base_prefix", venv
                ):
                    dry_run = setup_helpers.install_skill_bundle(None)
                    config = setup_helpers.mcp_config()
                    with self.assertRaisesRegex(ValueError, "install-skill needs a Super Saiyan Browser source repository"):
                        setup_helpers.install_skill_bundle(target)

                self.assertEqual(dry_run["status"], "source_unavailable")
                server = config["mcpServers"]["super-browser"]
                self.assertEqual(server["command"], sys.executable)
                self.assertEqual(server["args"], ["-m", "super_browser.mcp_server"])
                self.assertFalse(os.path.exists(os.path.join(target, "super-browser")))
        finally:
            if old_root is None:
                os.environ.pop("SUPER_BROWSER_REPO_ROOT", None)
            else:
                os.environ["SUPER_BROWSER_REPO_ROOT"] = old_root

    def test_packaged_asset_root_supports_install_skill_mcp_and_resources(self):
        old_root = os.environ.get("SUPER_BROWSER_REPO_ROOT")
        original_cwd = os.getcwd()
        try:
            os.environ.pop("SUPER_BROWSER_REPO_ROOT", None)
            with tempfile.TemporaryDirectory() as tmp:
                venv = os.path.join(tmp, "venv")
                package_dir = os.path.join(venv, "lib", "python3.14", "site-packages", "super_browser")
                package_setup = os.path.join(package_dir, "setup_helpers.py")
                package_mcp = os.path.join(package_dir, "mcp_server.py")
                asset_root = os.path.join(venv, "share", "super-browser")
                target = os.path.join(tmp, "skills")
                unrelated = os.path.join(tmp, "unrelated-project")
                os.makedirs(package_dir)
                os.makedirs(os.path.join(unrelated, "skills", "private-skill"))
                with open(package_setup, "w", encoding="utf-8") as handle:
                    handle.write("# installed package placeholder\n")
                with open(package_mcp, "w", encoding="utf-8") as handle:
                    handle.write("# installed mcp placeholder\n")
                _write_minimal_super_browser_root(asset_root)
                os.makedirs(os.path.join(asset_root, "skills", "playwright-specialist"), exist_ok=True)
                with open(os.path.join(asset_root, "references", "provider-matrix.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Provider Matrix\nBrowser Use\nPlaywright\n")
                with open(os.path.join(asset_root, "skills", "playwright-specialist", "SKILL.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Playwright Specialist\n")
                with open(os.path.join(unrelated, "README.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Private Project\n")
                with open(os.path.join(unrelated, "SKILL.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Private Skill\n")
                with open(os.path.join(unrelated, "skills", "private-skill", "SKILL.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Private Nested Skill\n")

                os.chdir(unrelated)
                with patch.object(setup_helpers, "__file__", package_setup), patch.object(mcp_server, "__file__", package_mcp), patch.object(
                    setup_helpers.sys, "prefix", venv
                ), patch.object(setup_helpers.sys, "base_prefix", venv):
                    dry_run = setup_helpers.install_skill_bundle(None)
                    installed = setup_helpers.install_skill_bundle(target)
                    config = setup_helpers.mcp_config()
                    resources = list_resources()
                    provider_doc = read_resource("super-browser://references/provider-matrix")

                resolved_asset_root = os.path.realpath(asset_root)
                installed_path = installed["installed_path"]
                server = config["mcpServers"]["super-browser"]
                uris = {resource["uri"] for resource in resources}
                self.assertEqual(dry_run["status"], "dry_run")
                self.assertEqual(dry_run["source"], resolved_asset_root)
                self.assertEqual(installed["status"], "installed")
                self.assertTrue(os.path.exists(os.path.join(installed_path, "SKILL.md")))
                self.assertTrue(os.path.exists(os.path.join(installed_path, "references", "provider-matrix.md")))
                self.assertTrue(os.path.exists(os.path.join(installed_path, "skills", "playwright-specialist", "SKILL.md")))
                self.assertFalse(os.path.exists(os.path.join(installed_path, "skills", "private-skill", "SKILL.md")))
                self.assertEqual(server["command"], sys.executable)
                self.assertEqual(server["args"], ["-m", "super_browser.mcp_server"])
                self.assertEqual(server["env"]["SUPER_BROWSER_REPO_ROOT"], resolved_asset_root)
                self.assertIn("super-browser://references/provider-matrix", uris)
                self.assertIn("super-browser://skills/playwright-specialist", uris)
                self.assertNotIn("super-browser://skills/private-skill", uris)
                self.assertIn("Browser Use", provider_doc["contents"][0]["text"])
        finally:
            os.chdir(original_cwd)
            if old_root is None:
                os.environ.pop("SUPER_BROWSER_REPO_ROOT", None)
            else:
                os.environ["SUPER_BROWSER_REPO_ROOT"] = old_root

    def test_cli_init_mcp_writes_config_with_absolute_server_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mcp.json")
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["init-mcp", "--path", path, "--cwd", os.getcwd()])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "written")
            with open(path, encoding="utf-8") as handle:
                config = json.load(handle)
            server = config["mcpServers"]["super-browser"]
            self.assertEqual(server["cwd"], os.getcwd())
            self.assertTrue(server["command"].endswith("mcp/super-browser-server"))
            self.assertEqual(server["env"]["SUPER_BROWSER_REPO_ROOT"], os.getcwd())

    def test_cli_init_mcp_merges_existing_config_without_dropping_servers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mcp.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"mcpServers": {"other": {"command": "other-server", "args": ["--ok"]}}}, handle)

            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["init-mcp", "--path", path, "--cwd", os.getcwd(), "--merge"])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "merged")
            with open(path, encoding="utf-8") as handle:
                config = json.load(handle)
            self.assertEqual(config["mcpServers"]["other"]["command"], "other-server")
            self.assertIn("super-browser", config["mcpServers"])
            self.assertEqual(config["mcpServers"]["super-browser"]["cwd"], os.getcwd())

    def test_cli_init_mcp_rejects_invalid_cwd_without_writing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            invalid_bundle = os.path.join(tmp, "empty-dir")
            path = os.path.join(tmp, "mcp.json")
            os.makedirs(invalid_bundle)
            output = io.StringIO()
            error_output = io.StringIO()

            with redirect_stdout(output), redirect_stderr(error_output):
                code = cli_main(["init-mcp", "--path", path, "--cwd", invalid_bundle])

            self.assertEqual(code, 1)
            self.assertIn("MCP cwd must point to a Super Saiyan Browser repository or installed bundle", error_output.getvalue())
            self.assertFalse(os.path.exists(path))

    def test_mcp_init_rejects_invalid_cwd_without_writing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            invalid_bundle = os.path.join(tmp, "empty-dir")
            path = os.path.join(tmp, "mcp.json")
            os.makedirs(invalid_bundle)

            with self.assertRaisesRegex(ValueError, "MCP cwd must point to a Super Saiyan Browser repository or installed bundle"):
                handle_tool("init_super_browser_mcp", {"path": path, "cwd": invalid_bundle})

            self.assertFalse(os.path.exists(path))

    def test_mcp_setup_tools_install_bundle_and_write_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "skills")
            install = handle_tool("install_super_browser_skill", {"target": target})
            installed_path = install["installed_path"]
            self.assertEqual(install["status"], "installed")
            self.assertTrue(os.path.exists(os.path.join(installed_path, "SKILL.md")))
            self.assertTrue(os.path.exists(os.path.join(installed_path, "mcp", "super-browser-server")))
            self.assertEqual(install["mcp_config"]["mcpServers"]["super-browser"]["cwd"], installed_path)

            existing_config = os.path.join(tmp, "mcp.json")
            with open(existing_config, "w", encoding="utf-8") as handle:
                json.dump({"mcpServers": {"other": {"command": "other-server"}}}, handle)

            written = handle_tool("init_super_browser_mcp", {"path": existing_config, "cwd": installed_path, "merge": True})
            self.assertEqual(written["status"], "merged")
            with open(existing_config, encoding="utf-8") as handle:
                config = json.load(handle)
            self.assertIn("other", config["mcpServers"])
            self.assertEqual(config["mcpServers"]["super-browser"]["cwd"], installed_path)

            dry_run = handle_tool("install_super_browser_skill", {})
            self.assertEqual(dry_run["status"], "dry_run")
            generated = handle_tool("init_super_browser_mcp", {"cwd": installed_path})
            self.assertEqual(generated["mcpServers"]["super-browser"]["cwd"], installed_path)

    def test_cli_doctor_reports_actionable_provider_readiness(self):
        env_names = [
            "DECODO_PROXY",
            "BROWSER_USE_API_KEY",
            "HYPERBROWSER_API_KEY",
            "BRIGHTDATA_API_KEY",
            "BRIGHTDATA_UNLOCKER_ZONE",
            "BRIGHTDATA_SERP_ZONE",
            "BRIGHTDATA_BROWSER_ZONE",
            "BRIGHTDATA_BROWSER_USERNAME",
            "BRIGHTDATA_BROWSER_PASSWORD",
            "BRIGHTDATA_CUSTOMER_ID",
        ]
        old_env = {name: os.environ.get(name) for name in env_names}
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            for name in env_names:
                os.environ.pop(name, None)
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                os.environ["HYPERBROWSER_API_KEY"] = "test-hyperbrowser-key"
                output = io.StringIO()
                with patch("super_browser.cli.load_env_file"), redirect_stdout(output):
                    code = cli_main(["doctor"])
                self.assertEqual(code, 0)
                payload = json.loads(output.getvalue())
                providers = {provider["name"]: provider for provider in payload["providers"]}

                playwright = providers["playwright"]
                self.assertTrue(playwright["production_ready"])
                self.assertEqual(playwright["production_ready_scope"], "local_verified")
                self.assertEqual(playwright["uncertified_workflow_classes"], [])
                self.assertFalse(playwright["requires_live_test_before_broader_production"])

                decodo = providers["decodo-http"]
                self.assertEqual(decodo["readiness_status"], "usable_direct_http_no_proxy")
                self.assertTrue(decodo["usable_now"])
                self.assertFalse(decodo["production_ready"])
                self.assertEqual(decodo["production_ready_scope"], "none")
                self.assertEqual(decodo["certified_workflow_classes"], [])
                self.assertEqual(decodo["supported_live_workflow_classes"], ["raw_http_direct"])
                self.assertEqual(decodo["uncertified_workflow_classes"], ["raw_http_direct"])
                self.assertTrue(decodo["requires_live_test_before_production"])
                self.assertFalse(decodo["requires_live_test_before_broader_production"])
                self.assertTrue(any("DECODO_PROXY" in blocker for blocker in decodo["production_blockers"]))
                self.assertTrue(any("raw_http_direct" in blocker for blocker in decodo["production_blockers"]))
                self.assertIsNone(decodo["latest_live_test"])
                self.assertIn("DECODO_PROXY", decodo["missing_optional_env"])
                self.assertIn("direct raw http", decodo["production_gate"].lower())

                browser_use = providers["browser-use"]
                self.assertEqual(browser_use["readiness_status"], "missing_env")
                self.assertFalse(browser_use["usable_now"])
                self.assertIn("BROWSER_USE_API_KEY", browser_use["missing_required_env"])
                self.assertFalse(browser_use["requires_live_test_before_production"])
                self.assertTrue(any("BROWSER_USE_API_KEY" in blocker for blocker in browser_use["production_blockers"]))
                self.assertIn("BROWSER_USE_API_KEY", browser_use["next_action"])

                hyperbrowser = providers["hyperbrowser"]
                self.assertEqual(hyperbrowser["readiness_status"], "configured_live_test_required")
                self.assertTrue(hyperbrowser["usable_now"])
                self.assertFalse(hyperbrowser["production_ready"])
                self.assertEqual(hyperbrowser["production_ready_scope"], "none")
                self.assertEqual(hyperbrowser["certified_workflow_classes"], [])
                self.assertEqual(hyperbrowser["supported_live_workflow_classes"], ["general_read", "external_write_gate"])
                self.assertEqual(hyperbrowser["uncertified_workflow_classes"], ["general_read", "external_write_gate"])
                self.assertTrue(hyperbrowser["requires_live_test_before_production"])
                self.assertTrue(any("general_read" in blocker for blocker in hyperbrowser["production_blockers"]))
                self.assertIn("live-test --provider hyperbrowser", hyperbrowser["next_action"])
        finally:
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_doctor_requires_playwright_for_steel_adapter(self):
        old_api_key = os.environ.get("STEEL_API_KEY")
        try:
            os.environ["STEEL_API_KEY"] = "test-steel-key"

            def fake_find_spec(name):
                if name == "playwright.sync_api":
                    return None
                return object()

            with patch("super_browser.providers.importlib.util.find_spec", side_effect=fake_find_spec):
                payload = handle_tool("browser_doctor", {})

            steel = {provider["name"]: provider for provider in payload["providers"]}["steel"]
            self.assertEqual(steel["readiness_status"], "package_missing")
            self.assertFalse(steel["usable_now"])
            self.assertFalse(steel["configured"])
            self.assertFalse(steel["python_package_available"])
            self.assertIn("provider package or CLI is missing", steel["production_blockers"])
        finally:
            if old_api_key is None:
                os.environ.pop("STEEL_API_KEY", None)
            else:
                os.environ["STEEL_API_KEY"] = old_api_key

    def test_doctor_and_production_gate_block_playwright_when_browser_runtime_is_missing(self):
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")

        class BrokenPlaywrightContext:
            def __enter__(self):
                raise RuntimeError("Executable does not exist. Please run playwright install chromium.")

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakePlaywrightModule:
            @staticmethod
            def sync_playwright():
                return BrokenPlaywrightContext()

        def fake_find_spec(name):
            if name == "playwright.sync_api":
                return object()
            return None

        def fake_import_module(name):
            if name == "playwright.sync_api":
                return FakePlaywrightModule
            raise ModuleNotFoundError(name)

        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                runtime_probe = getattr(__import__("super_browser.providers", fromlist=["_playwright_runtime_available"]), "_playwright_runtime_available", None)
                if runtime_probe and hasattr(runtime_probe, "cache_clear"):
                    runtime_probe.cache_clear()

                with patch("super_browser.providers.importlib.util.find_spec", side_effect=fake_find_spec), patch(
                    "super_browser.providers.importlib.import_module",
                    side_effect=fake_import_module,
                ):
                    doctor_payload = handle_tool("browser_doctor", {})
                    readiness_payload = production_readiness(required_providers=["playwright"])

            playwright = {provider["name"]: provider for provider in doctor_payload["providers"]}["playwright"]
            self.assertEqual(playwright["readiness_status"], "runtime_missing")
            self.assertFalse(playwright["usable_now"])
            self.assertFalse(playwright["configured"])
            self.assertFalse(playwright["production_ready"])
            self.assertEqual(playwright["production_ready_scope"], "none")
            self.assertFalse(playwright["browser_runtime_available"])
            self.assertTrue(any("browser runtime is missing" in blocker for blocker in playwright["production_blockers"]))
            self.assertIn("playwright install chromium", playwright["production_gate"])
            self.assertIn("playwright install chromium", playwright["next_action"])

            self.assertFalse(readiness_payload["production_ready"])
            self.assertEqual(readiness_payload["status"], "blocked")
            self.assertIn("playwright", readiness_payload["blocked_providers"])
            self.assertEqual(readiness_payload["ready_providers"], [])
            self.assertTrue(any("playwright install chromium" in action for action in readiness_payload["next_actions"]))
        finally:
            runtime_probe = getattr(__import__("super_browser.providers", fromlist=["_playwright_runtime_available"]), "_playwright_runtime_available", None)
            if runtime_probe and hasattr(runtime_probe, "cache_clear"):
                runtime_probe.cache_clear()
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_doctor_reprobes_playwright_runtime_after_initial_missing_probe(self):
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        runtime_installed = {"value": False}

        class BrokenPlaywrightContext:
            def __enter__(self):
                raise RuntimeError("Executable does not exist. Please run playwright install chromium.")

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeBrowser:
            def close(self):
                return None

        class FakeChromium:
            def launch(self, headless=True):
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

        class FixedPlaywrightContext:
            def __enter__(self):
                return FakePlaywright()

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakePlaywrightModule:
            @staticmethod
            def sync_playwright():
                if runtime_installed["value"]:
                    return FixedPlaywrightContext()
                return BrokenPlaywrightContext()

        def fake_find_spec(name):
            if name == "playwright.sync_api":
                return object()
            return None

        def fake_import_module(name):
            if name == "playwright.sync_api":
                return FakePlaywrightModule
            raise ModuleNotFoundError(name)

        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                runtime_probe = getattr(__import__("super_browser.providers", fromlist=["_playwright_runtime_available"]), "_playwright_runtime_available", None)
                if runtime_probe and hasattr(runtime_probe, "cache_clear"):
                    runtime_probe.cache_clear()

                with patch("super_browser.providers.importlib.util.find_spec", side_effect=fake_find_spec), patch(
                    "super_browser.providers.importlib.import_module",
                    side_effect=fake_import_module,
                ):
                    first = handle_tool("browser_doctor", {})
                    runtime_installed["value"] = True
                    second = handle_tool("browser_doctor", {})

            first_playwright = {provider["name"]: provider for provider in first["providers"]}["playwright"]
            second_playwright = {provider["name"]: provider for provider in second["providers"]}["playwright"]
            self.assertEqual(first_playwright["readiness_status"], "runtime_missing")
            self.assertEqual(second_playwright["readiness_status"], "ready_local")
            self.assertTrue(second_playwright["browser_runtime_available"])
        finally:
            runtime_probe = getattr(__import__("super_browser.providers", fromlist=["_playwright_runtime_available"]), "_playwright_runtime_available", None)
            if runtime_probe and hasattr(runtime_probe, "cache_clear"):
                runtime_probe.cache_clear()
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_cli_get_and_runs_are_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            approval_run = create_run("Post this comment on LinkedIn", execute=False)

            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["get", run.run_id])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["run_id"], run.run_id)
            self.assertEqual(payload["status"], "planned")
            self.assertEqual([event["type"] for event in payload["events"]], ["run_created"])

            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["runs"])
            self.assertEqual(code, 0)
            runs = json.loads(output.getvalue())
            listed = {item["run_id"]: item for item in runs}
            self.assertIn(run.run_id, listed)
            self.assertEqual(listed[run.run_id]["status"], "planned")
            self.assertEqual(listed[run.run_id]["event_count"], 1)
            self.assertNotIn("events", listed[run.run_id])

            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["runs", "--status", "awaiting_approval", "--limit", "1"])
            self.assertEqual(code, 0)
            filtered = json.loads(output.getvalue())
            self.assertEqual([item["run_id"] for item in filtered], [approval_run.run_id])

            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["runs", "--status", "planned", "--details"])
            self.assertEqual(code, 0)
            detailed = json.loads(output.getvalue())
            detailed_by_id = {item["run_id"]: item for item in detailed}
            self.assertEqual([event["type"] for event in detailed_by_id[run.run_id]["events"]], ["run_created"])

    def test_cli_handoff_is_read_only_and_agent_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Post this comment on LinkedIn", execute=False)

            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["handoff", run.run_id])

            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["type"], "super_browser_run_handoff")
            self.assertEqual(payload["run_id"], run.run_id)
            self.assertEqual(payload["status"], "awaiting_approval")
            self.assertTrue(payload["approval"]["pending"])
            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertTrue(payload["verification"]["policy_guard"]["approval_required"])
            self.assertEqual(payload["verification"]["policy_guard"]["approval_status"], "pending")
            self.assertTrue(payload["verification"]["policy_guard"]["safety_stop"])
            self.assertEqual(payload["verification"]["plan_integrity"]["status"], "unavailable")
            self.assertIn("super-browser resume", payload["commands"]["resume"])
            self.assertIn("handoff_browser_run", payload["mcp"])
            self.assertIn("provider_readiness", payload)
            self.assertIn("Resolve the pending approval before execution.", payload["agent_next_steps"])

            stored = RunStore().get(run.run_id)
            self.assertEqual([event["type"] for event in stored["events"]], ["run_created"])

    def test_handoff_marks_target_scope_safety_stop_unsafe_to_resume(self):
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

                payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

                self.assertFalse(payload["resume"]["safe_to_resume"])
                self.assertFalse(payload["resume"]["will_execute_provider"])
                self.assertIn("target-scope or DNS safety stop", payload["resume"]["reason"])
                self.assertTrue(payload["verification"]["policy_guard"]["non_resumable_safety_stop"])
                self.assertEqual(payload["verification"]["policy_guard"]["non_resumable_reason"], "raw_http_resolved_target_scope")
                self.assertTrue(any("target-scope or DNS safety stop" in step for step in payload["agent_next_steps"]))
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy

    def test_handoff_marks_approval_integrity_mismatch_unsafe_to_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            approved = approve_run(run.run_id, approver="tester", reason="approved exact message")
            approved.plan["rationale"].append("tampered after approval")
            RunStore().save(approved)

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertIn("approval integrity", payload["resume"]["reason"])
            self.assertEqual(payload["verification"]["approval_integrity"]["status"], "mismatch")
            self.assertTrue(any("Do not resume" in step for step in payload["agent_next_steps"]))

    def test_handoff_marks_expired_approval_as_non_executing_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            approved = approve_run(run.run_id, approver="tester", reason="approved exact message")
            approved.approvals[-1]["decided_at"] = "2000-01-01T00:00:00+00:00"
            RunStore().save(approved)

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertTrue(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertIn("approval expired", payload["resume"]["reason"])
            self.assertEqual(payload["verification"]["approval_expiry"]["status"], "expired")
            self.assertIn("approval_expired", [failure["type"] for failure in payload["verification"]["failures"]])
            self.assertTrue(any("fresh approval" in step for step in payload["agent_next_steps"]))

    def test_handoff_marks_missing_approval_record_unsafe_to_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Send a message in the browser")
            approved = approve_run(run.run_id, approver="tester", reason="approved exact message")
            approved.approvals = []
            RunStore().save(approved)

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertIn("approval integrity", payload["resume"]["reason"])
            self.assertEqual(payload["verification"]["approval_integrity"]["status"], "missing")
            self.assertEqual(payload["verification"]["policy_guard"]["approval_status"], "missing")
            self.assertIn("missing_approval_record", [failure["type"] for failure in payload["verification"]["failures"]])
            self.assertTrue(any("Do not resume" in step for step in payload["agent_next_steps"]))

    def test_handoff_uses_policy_guard_when_stored_safety_flags_are_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Post this comment on LinkedIn", execute=False)
            run.status = "planned"
            run.plan["approval_required"] = False
            run.plan["task"]["external_write"] = False
            run.plan["task"]["draft_only"] = True
            run.approvals = []
            RunStore().save(run)

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(RunStore().get(run.run_id)["plan"]["task"]["external_write"])
            self.assertTrue(payload["task"]["external_write"])
            self.assertFalse(payload["task"]["draft_only"])
            self.assertTrue(payload["route"]["approval_required"])
            self.assertTrue(payload["approval"]["required"])
            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertEqual(payload["verification"]["policy_guard"]["approval_status"], "missing")
            self.assertIn("missing_approval_record", [failure["type"] for failure in payload["verification"]["failures"]])
            self.assertTrue(any("approval evidence" in step for step in payload["agent_next_steps"]))

    def test_handoff_derives_draft_only_when_stored_draft_flag_is_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Draft a LinkedIn comment, put it in the box, but do not publish", execute=False)
            run.plan["task"]["draft_only"] = False
            RunStore().save(run)

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(RunStore().get(run.run_id)["plan"]["task"]["draft_only"])
            self.assertTrue(payload["task"]["draft_only"])
            self.assertTrue(payload["verification"]["policy_guard"]["draft_only"])
            self.assertFalse(payload["task"]["external_write"])
            self.assertFalse(payload["route"]["approval_required"])

    def test_handoff_uses_policy_guard_when_stored_auth_flag_is_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Use my logged in Chrome session to read private dashboard notifications", execute=False)
            run.status = "planned"
            run.plan["approval_required"] = False
            run.plan["task"]["requires_auth"] = False
            run.approvals = []
            RunStore().save(run)

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(RunStore().get(run.run_id)["plan"]["task"]["requires_auth"])
            self.assertTrue(payload["task"]["requires_auth"])
            self.assertTrue(payload["route"]["approval_required"])
            self.assertTrue(payload["approval"]["required"])
            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertTrue(payload["verification"]["policy_guard"]["requires_auth"])
            self.assertEqual(payload["verification"]["policy_guard"]["approval_status"], "missing")
            self.assertIn("missing_approval_record", [failure["type"] for failure in payload["verification"]["failures"]])

    def test_handoff_uses_policy_guard_when_stored_long_running_flag_is_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Monitor this public page overnight and report changes", execute=False)
            run.plan["task"]["long_running"] = False
            RunStore().save(run)

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(RunStore().get(run.run_id)["plan"]["task"]["long_running"])
            self.assertTrue(payload["task"]["long_running"])
            self.assertTrue(payload["verification"]["policy_guard"]["long_running"])

    def test_handoff_uses_url_derived_target_scope_when_stored_scope_is_stale(self):
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

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertEqual(RunStore().get(run.run_id)["plan"]["task"]["target_scope"], "public_web")
            self.assertEqual(payload["verification"]["policy_guard"]["target_scope"], "link_local")
            self.assertTrue(payload["route"]["approval_required"])
            self.assertTrue(payload["approval"]["required"])
            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertIn("provider sequence", payload["resume"]["reason"])
            self.assertIn("provider_target_scope_mismatch", [failure["type"] for failure in payload["verification"]["failures"]])

    def test_handoff_marks_provider_allowlist_violation_unsafe_to_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", providers_allowed=["playwright"], execute=False)
            run.plan["primary_provider"] = "decodo-http"
            run.plan["fallback_providers"] = []
            RunStore().save(run)

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertIn("provider sequence", payload["resume"]["reason"])
            self.assertIn("provider_allowlist_violation", [failure["type"] for failure in payload["verification"]["failures"]])
            self.assertTrue(any("provider sequence" in step for step in payload["agent_next_steps"]))

    def test_handoff_marks_invalid_task_constraints_unsafe_to_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            run.plan["task"]["max_cost_usd"] = math.nan
            RunStore().save(run)

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertIn("provider sequence", payload["resume"]["reason"])
            self.assertIn("provider_constraint_invalid_task", [failure["type"] for failure in payload["verification"]["failures"]])
            self.assertTrue(any("provider sequence" in step for step in payload["agent_next_steps"]))

    def test_handoff_marks_raw_http_without_http_url_unsafe_to_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Search the web for public mentions of this brand", execute=False)
            run.plan["task"]["raw_http"] = True
            RunStore().save(run)

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertIn("provider sequence", payload["resume"]["reason"])
            self.assertIn("provider_raw_http_url_constraint_violation", [failure["type"] for failure in payload["verification"]["failures"]])

    def test_handoff_marks_plan_integrity_mismatch_unsafe_to_resume(self):
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

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertIn("plan integrity", payload["resume"]["reason"])
            self.assertEqual(payload["verification"]["plan_integrity"]["status"], "mismatch")
            self.assertTrue(any("run-report evidence" in step for step in payload["agent_next_steps"]))

    def test_handoff_marks_untrusted_artifact_path_unsafe_to_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = os.path.join(tmp, "state")
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
                        "secret": "needle-handoff-secret",
                    },
                    handle,
                )
            run.status = "failed"
            run.artifacts.append({"type": "run_report", "provider": "playwright", "path": report_path})
            run.verification = {"confidence": "medium", "selected_provider": "playwright", "checks": ["simulated failed run"]}
            RunStore().save(run)

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertIn("artifact evidence", payload["resume"]["reason"])
            self.assertIn("untrusted_artifact_path", [failure["type"] for failure in payload["verification"]["failures"]])
            self.assertEqual(payload["verification"]["run_report_path"], None)
            self.assertNotIn("needle-handoff-secret", json.dumps(payload))

    def test_handoff_marks_run_report_run_id_mismatch_unsafe_to_resume(self):
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

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertIn("artifact evidence", payload["resume"]["reason"])
            self.assertIn("run_report_run_id_mismatch", [failure["type"] for failure in payload["verification"]["failures"]])

    def test_handoff_marks_invalid_run_id_payload_unsafe_to_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            run.run_id = ".."
            run.status = "planned"
            run.verification = {"confidence": "medium", "selected_provider": "playwright", "checks": ["simulated corrupted run id"]}
            RunStore().save(run)

            payload = handle_tool("handoff_browser_run", {"run_id": ".."})

            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertIn("artifact evidence", payload["resume"]["reason"])
            self.assertIn("invalid_run_id", [failure["type"] for failure in payload["verification"]["failures"]])
            self.assertEqual(payload["verification"]["run_id_integrity"]["status"], "invalid")

    def test_handoff_marks_run_report_status_mismatch_unsafe_to_resume(self):
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

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertIn("final_status", payload["resume"]["reason"])
            self.assertEqual(payload["verification"]["plan_integrity"]["status"], "verified")
            self.assertIn("status_mismatch", [failure["type"] for failure in payload["verification"]["failures"]])
            self.assertTrue(any("final_status" in step for step in payload["agent_next_steps"]))

    def test_handoff_marks_run_report_attempt_integrity_unsafe_to_resume(self):
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

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertIn("artifact evidence", payload["resume"]["reason"])
            self.assertIn("run_report_final_provider_attempt_missing", [failure["type"] for failure in payload["verification"]["failures"]])
            self.assertTrue(any("artifact evidence" in step for step in payload["agent_next_steps"]))

    def test_handoff_marks_missing_run_report_unsafe_to_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            run.status = "failed"
            run.verification = {"confidence": "medium", "selected_provider": "playwright", "checks": ["simulated failed run without report"]}
            RunStore().save(run)

            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertFalse(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertIn("artifact evidence", payload["resume"]["reason"])
            self.assertIn("missing_run_report", [failure["type"] for failure in payload["verification"]["failures"]])
            self.assertTrue(any("artifact evidence" in step for step in payload["agent_next_steps"]))

    def test_handoff_external_write_failure_says_resume_creates_retry_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Post this comment on LinkedIn")
            failed_result = ExecutionResult(provider="browser-use", status="failed", error="simulated write failure")
            with patch("super_browser.runtime.execute_plan", return_value=failed_result):
                attempted = approve_run(run.run_id, approver="tester", reason="approved initial write", execute=True)

            payload = handle_tool("handoff_browser_run", {"run_id": attempted.run_id})

            self.assertTrue(payload["resume"]["safe_to_resume"])
            self.assertFalse(payload["resume"]["will_execute_provider"])
            self.assertTrue(payload["resume"]["fresh_retry_approval_required"])
            self.assertIn("fresh retry approval", payload["resume"]["reason"])
            self.assertTrue(payload["verification"]["write_retry_guard"]["fresh_retry_approval_required"])
            self.assertTrue(any("fresh retry approval" in step for step in payload["agent_next_steps"]))

    def test_handoff_approved_retry_says_resume_will_execute_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Post this comment on LinkedIn")
            failed_result = ExecutionResult(provider="browser-use", status="failed", error="simulated write failure")
            with patch("super_browser.runtime.execute_plan", return_value=failed_result):
                approve_run(run.run_id, approver="tester", reason="approved initial write", execute=True)
            retry_gate = resume_run(run.run_id)
            approved_retry = approve_run(retry_gate.run_id, approver="tester", reason="approved retry")

            payload = handle_tool("handoff_browser_run", {"run_id": approved_retry.run_id})

            self.assertTrue(payload["resume"]["safe_to_resume"])
            self.assertTrue(payload["resume"]["will_execute_provider"])
            self.assertFalse(payload["resume"]["fresh_retry_approval_required"])
            self.assertEqual(payload["resume"]["reason"], "run status is approved")
            self.assertTrue(payload["verification"]["write_retry_guard"]["retry_approval_after_last_attempt"])

    def test_cli_handoff_missing_run_does_not_create_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = os.path.join(tmp, "missing-state")
            os.environ["SUPER_BROWSER_STATE_DIR"] = state_dir
            output = io.StringIO()
            error_output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(error_output):
                code = cli_main(["handoff", "run_missing"])
            self.assertEqual(code, 1)
            self.assertIn("Run not found: run_missing", error_output.getvalue())
            self.assertFalse(os.path.exists(state_dir))

    def test_cli_empty_runs_does_not_create_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = os.path.join(tmp, "missing-state")
            os.environ["SUPER_BROWSER_STATE_DIR"] = state_dir
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["runs"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue()), [])
            self.assertFalse(os.path.exists(state_dir))

    def test_mcp_plan_tool(self):
        payload = handle_tool("plan_browser_task", {"goal": "Use a desktop computer to open a spreadsheet"})
        self.assertEqual(payload["primary_provider"], "orgo")
        self.assertIn("council_report", payload)
        self.assertEqual(payload["council_report"]["mode"], "council")
        self.assertNotIn("ORGO_COMPUTER_ID", payload["council_report"]["planner_decision"]["missing_env"])

    def test_mcp_tools_expose_input_schemas(self):
        tools = {tool["name"]: tool for tool in TOOLS}
        self.assertIn("inputSchema", tools["plan_browser_task"])
        self.assertEqual(tools["plan_browser_task"]["inputSchema"]["required"], ["goal"])
        self.assertIn("providers_allowed", tools["plan_browser_task"]["inputSchema"]["properties"])
        self.assertEqual(tools["plan_browser_task"]["inputSchema"]["properties"]["timeout_seconds"]["type"], "integer")
        provider_enum = tools["plan_browser_task"]["inputSchema"]["properties"]["providers_allowed"]["items"]["enum"]
        self.assertIn("browser-use", provider_enum)
        self.assertEqual(tools["resume_browser_run"]["inputSchema"]["required"], ["run_id"])
        self.assertEqual(tools["handoff_browser_run"]["inputSchema"]["required"], ["run_id"])
        self.assertEqual(tools["approve_browser_run"]["inputSchema"]["required"], ["run_id", "reason"])
        self.assertEqual(tools["deny_browser_run"]["inputSchema"]["required"], ["run_id", "reason"])
        self.assertEqual(tools["approve_browser_run"]["inputSchema"]["properties"]["reason"]["minLength"], 1)
        self.assertEqual(tools["approve_browser_run"]["inputSchema"]["properties"]["by"]["minLength"], 1)
        self.assertEqual(tools["deny_browser_run"]["inputSchema"]["properties"]["by"]["minLength"], 1)
        self.assertEqual(tools["list_browser_providers"]["inputSchema"]["additionalProperties"], False)
        self.assertEqual(tools["production_readiness"]["inputSchema"]["additionalProperties"], False)
        self.assertTrue(tools["production_readiness"]["annotations"]["readOnlyHint"])
        self.assertEqual(tools["bundle_manifest"]["inputSchema"]["additionalProperties"], False)
        self.assertTrue(tools["bundle_manifest"]["annotations"]["readOnlyHint"])
        self.assertEqual(tools["env_checklist"]["inputSchema"]["additionalProperties"], False)
        self.assertTrue(tools["env_checklist"]["annotations"]["readOnlyHint"])
        self.assertEqual(tools["list_browser_runs"]["inputSchema"]["additionalProperties"], False)
        self.assertEqual(tools["list_browser_runs"]["inputSchema"]["properties"]["limit"]["type"], "integer")
        self.assertIn("target", tools["install_super_browser_skill"]["inputSchema"]["properties"])
        self.assertIn("path", tools["init_super_browser_mcp"]["inputSchema"]["properties"])
        self.assertTrue(tools["install_super_browser_skill"]["annotations"]["destructiveHint"])
        self.assertTrue(tools["init_super_browser_mcp"]["annotations"]["destructiveHint"])
        self.assertIn("awaiting_approval", tools["list_browser_runs"]["inputSchema"]["properties"]["status"]["enum"])
        workflow_enum = tools["run_browser_live_tests"]["inputSchema"]["properties"]["workflow_class"]["enum"]
        self.assertIn("raw_http_direct", workflow_enum)
        self.assertIn("authenticated_read", workflow_enum)
        self.assertIn("external_write_gate", workflow_enum)
        self.assertIn("authenticated_write_profile", workflow_enum)
        self.assertIn("fleet_read", workflow_enum)
        self.assertTrue(tools["get_browser_run"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["handoff_browser_run"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["list_browser_runs"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["run_browser_task"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["resume_browser_run"]["annotations"]["destructiveHint"])
        self.assertTrue(tools["resume_browser_run"]["annotations"]["openWorldHint"])
        self.assertTrue(tools["approve_browser_run"]["annotations"]["destructiveHint"])
        self.assertTrue(tools["approve_browser_run"]["annotations"]["openWorldHint"])
        self.assertFalse(tools["deny_browser_run"]["annotations"]["destructiveHint"])

    def test_mcp_production_readiness_reports_blockers(self):
        old_env = {name: os.environ.get(name) for name in [
            "BROWSER_USE_API_KEY",
            "ORGO_API_KEY",
            "ORGO_COMPUTER_ID",
            "AIRTOP_API_KEY",
            "HYPERBROWSER_API_KEY",
            "STEEL_API_KEY",
            "DECODO_PROXY",
        ]}
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            for name in old_env:
                os.environ.pop(name, None)
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                payload = handle_tool("production_readiness", {})

            self.assertFalse(payload["production_ready"])
            self.assertIn("browser-use", payload["blocked_providers"])
            self.assertIn("BROWSER_USE_API_KEY", payload["missing_env"])
            self.assertIn("playwright", payload["ready_providers"])
            self.assertIn("decodo-http", payload["uncertified_providers"])
        finally:
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_mcp_bundle_manifest_returns_redacted_inventory(self):
        payload = handle_tool("bundle_manifest", {})

        self.assertEqual(payload["type"], "super_browser_bundle_manifest")
        self.assertEqual(payload["status"], "ok")
        self.assertIn("bundle_manifest", payload["mcp_tools"])
        self.assertIn("super-browser://README", payload["resources"])
        self.assertIn("super-browser://skills/browser-use-specialist", payload["resources"])
        self.assertTrue(payload["entrypoints"]["cli"]["present"])
        self.assertTrue(payload["entrypoints"]["mcp_server"]["present"])
        relative_files = {item["path"] for item in payload["files"]}
        self.assertIn("SKILL.md", relative_files)
        self.assertNotIn(".env", relative_files)

    def test_mcp_env_checklist_returns_redacted_setup_status(self):
        payload = handle_tool("env_checklist", {})

        self.assertEqual(payload["type"], "super_browser_env_checklist")
        self.assertFalse(payload["values_included"])
        self.assertIn("providers", payload)
        self.assertIn("missing_required_env", payload)
        self.assertIn("BROWSER_USE_API_KEY", {item["name"] for item in payload["all_env"]})
        self.assertIn("production-readiness", " ".join(payload["commands"]))
        self.assertNotIn("=", json.dumps(payload["all_env"]))

    def test_mcp_setup_walkthrough_returns_steps(self):
        payload = handle_tool("setup_walkthrough", {"client": "codex"})
        self.assertEqual(payload["type"], "super_browser_setup_walkthrough")
        self.assertGreaterEqual(len(payload["provider_signup"]), 5)
        self.assertEqual(payload["client_hint"], "codex")

    def test_production_readiness_keeps_playwright_locally_ready_after_partial_live_evidence(self):
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")

        def fake_find_spec(name):
            if name == "playwright.sync_api":
                return object()
            return None

        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                record_live_test_evidence(
                    {
                        "results": [
                            {
                                "provider": "playwright",
                                "status": "passed",
                                "workflow_class": "local_browser_fixture",
                                "verification": {"confidence": "high", "checks": ["fake local fixture passed"]},
                            }
                        ]
                    },
                    "playwright",
                    {"playwright"},
                )

                with patch("super_browser.providers.importlib.util.find_spec", side_effect=fake_find_spec), patch(
                    "super_browser.providers._playwright_runtime_available",
                    return_value=(True, None),
                ):
                    readiness = production_readiness(required_providers=["playwright"])
                    doctor_payload = handle_tool("browser_doctor", {})

            playwright = {provider["name"]: provider for provider in doctor_payload["providers"]}["playwright"]
            self.assertEqual(playwright["readiness_status"], "ready_local")
            self.assertTrue(playwright["production_ready"])
            self.assertEqual(playwright["production_ready_scope"], "local_verified")
            self.assertEqual(playwright["uncertified_workflow_classes"], [])
            self.assertEqual(playwright["certified_workflow_classes"], ["local_browser_fixture"])

            self.assertTrue(readiness["production_ready"])
            self.assertEqual(readiness["status"], "ready")
            self.assertEqual(readiness["ready_providers"], ["playwright"])
            self.assertEqual(readiness["blocked_providers"], [])
            self.assertEqual(readiness["uncertified_providers"], [])
        finally:
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_production_readiness_blocks_partial_provider_certification(self):
        rows = [
            {
                "name": "decodo-http",
                "display_name": "Decodo Raw HTTP",
                "production_ready": True,
                "production_ready_scope": "workflow_class:raw_http_direct",
                "readiness_status": "live_test_passed",
                "missing_required_env": [],
                "missing_optional_env": ["DECODO_PROXY"],
                "uncertified_workflow_classes": ["external_write_gate"],
                "production_blockers": ["missing fresh live-test evidence for workflow classes: external_write_gate"],
                "production_gate": "Fresh live-test evidence exists for workflow class: raw_http_direct. Run task-class live tests before broader production use.",
            }
        ]
        with patch("super_browser.production.provider_readiness", return_value=rows):
            payload = production_readiness(required_providers=["decodo-http"])

        self.assertFalse(payload["production_ready"])
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["ready_providers"], [])
        self.assertEqual(payload["blocked_providers"], ["decodo-http"])
        self.assertEqual(payload["uncertified_providers"], ["decodo-http"])
        self.assertIn("external_write_gate", payload["blockers"][0]["uncertified_workflow_classes"])

    def test_mcp_json_rpc_initialize_and_tool_call_include_structured_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = os.path.join(tmp, "missing-state")
            os.environ["SUPER_BROWSER_STATE_DIR"] = state_dir
            stdin = io.StringIO(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "initialize",
                                "params": {
                                    "protocolVersion": "2025-06-18",
                                    "capabilities": {},
                                    "clientInfo": {"name": "test", "version": "1"},
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 2,
                                "method": "tools/call",
                                "params": {"name": "list_browser_runs", "arguments": {}},
                            }
                        ),
                    ]
                )
                + "\n"
            )
            output = io.StringIO()
            with patch("sys.stdin", stdin), redirect_stdout(output):
                code = mcp_main()
            self.assertEqual(code, 0)
            responses = [json.loads(line) for line in output.getvalue().splitlines()]
            initialize = responses[0]["result"]
            self.assertEqual(initialize["protocolVersion"], "2025-06-18")
            self.assertIn("resources", initialize["capabilities"])
            self.assertIn("instructions", initialize)
            tool_result = responses[1]["result"]
            self.assertEqual(tool_result["structuredContent"], [])
            self.assertEqual(json.loads(tool_result["content"][0]["text"]), tool_result["structuredContent"])
            self.assertFalse(os.path.exists(state_dir))

    def test_mcp_json_rpc_notifications_are_consumed_without_response(self):
        stdin = io.StringIO(
            "\n".join(
                [
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                    json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}),
                    json.dumps({"jsonrpc": "2.0", "method": "tools/list", "params": {}}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                ]
            )
            + "\n"
        )
        output = io.StringIO()
        with patch("sys.stdin", stdin), redirect_stdout(output):
            code = mcp_main()
        self.assertEqual(code, 0)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([response["id"] for response in responses], [1, 2])
        self.assertIn("result", responses[0])
        self.assertIn("tools", responses[1]["result"])

    def test_mcp_json_rpc_resources_list_and_read_provider_docs(self):
        old_root = os.environ.get("SUPER_BROWSER_REPO_ROOT")
        os.environ["SUPER_BROWSER_REPO_ROOT"] = os.getcwd()
        try:
            stdin = io.StringIO(
                "\n".join(
                    [
                        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "resources/list"}),
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 2,
                                "method": "resources/read",
                                "params": {"uri": "super-browser://references/provider-matrix"},
                            }
                        ),
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 3,
                                "method": "resources/read",
                                "params": {"uri": "super-browser://skills/playwright-specialist"},
                            }
                        ),
                    ]
                )
                + "\n"
            )
            output = io.StringIO()
            with patch("sys.stdin", stdin), redirect_stdout(output):
                code = mcp_main()
            self.assertEqual(code, 0)
            responses = [json.loads(line) for line in output.getvalue().splitlines()]
            resources = responses[0]["result"]["resources"]
            uris = {resource["uri"] for resource in resources}
            self.assertIn("super-browser://references/provider-matrix", uris)
            self.assertIn("super-browser://skills/playwright-specialist", uris)
            provider_text = responses[1]["result"]["contents"][0]["text"]
            self.assertIn("Browser Use", provider_text)
            self.assertIn("Playwright", provider_text)
            skill_text = responses[2]["result"]["contents"][0]["text"]
            self.assertIn("playwright", skill_text.lower())
        finally:
            if old_root is None:
                os.environ.pop("SUPER_BROWSER_REPO_ROOT", None)
            else:
                os.environ["SUPER_BROWSER_REPO_ROOT"] = old_root

    def test_mcp_resources_do_not_follow_symlinks_outside_repo_root(self):
        old_root = os.environ.get("SUPER_BROWSER_REPO_ROOT")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = os.path.join(tmp, "repo")
                outside = os.path.join(tmp, "outside-secret.md")
                normal_skill = os.path.join(root, "skills", "normal")
                leaky_skill = os.path.join(root, "skills", "leaky")
                os.makedirs(normal_skill)
                os.makedirs(leaky_skill)
                os.makedirs(os.path.join(root, "references"))
                os.makedirs(os.path.join(root, "scripts"))
                os.makedirs(os.path.join(root, "mcp"))
                with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Super Saiyan Browser\n")
                with open(os.path.join(root, "SKILL.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Super Saiyan Browser Skill\n")
                with open(os.path.join(root, "scripts", "super-browser"), "w", encoding="utf-8") as handle:
                    handle.write("#!/usr/bin/env bash\n")
                with open(os.path.join(root, "mcp", "super-browser-server"), "w", encoding="utf-8") as handle:
                    handle.write("#!/usr/bin/env bash\n")
                with open(os.path.join(normal_skill, "SKILL.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Normal Skill\n")
                with open(outside, "w", encoding="utf-8") as handle:
                    handle.write("outside secret")
                try:
                    os.symlink(outside, os.path.join(leaky_skill, "SKILL.md"))
                except (AttributeError, NotImplementedError, OSError) as exc:
                    self.skipTest(f"symlink creation unavailable: {exc}")

                os.environ["SUPER_BROWSER_REPO_ROOT"] = root
                uris = {resource["uri"] for resource in list_resources()}
                self.assertIn("super-browser://skills/normal", uris)
                self.assertNotIn("super-browser://skills/leaky", uris)
                with self.assertRaisesRegex(ValueError, "Resource path escapes Super Saiyan Browser root"):
                    read_resource("super-browser://skills/leaky")
        finally:
            if old_root is None:
                os.environ.pop("SUPER_BROWSER_REPO_ROOT", None)
            else:
                os.environ["SUPER_BROWSER_REPO_ROOT"] = old_root

    def test_package_only_mcp_resources_do_not_expose_current_working_directory(self):
        old_root = os.environ.get("SUPER_BROWSER_REPO_ROOT")
        original_cwd = os.getcwd()
        try:
            os.environ.pop("SUPER_BROWSER_REPO_ROOT", None)
            with tempfile.TemporaryDirectory() as tmp:
                package_file = os.path.join(tmp, "venv", "lib", "python3.14", "site-packages", "super_browser", "mcp_server.py")
                unrelated = os.path.join(tmp, "unrelated-project")
                os.makedirs(os.path.dirname(package_file))
                os.makedirs(os.path.join(unrelated, "skills", "private-skill"))
                with open(os.path.join(unrelated, "README.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Private Project\n")
                with open(os.path.join(unrelated, "SKILL.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Private Skill\n")
                with open(os.path.join(unrelated, "skills", "private-skill", "SKILL.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Private Nested Skill\n")

                os.chdir(unrelated)
                with patch.object(mcp_server, "__file__", package_file):
                    self.assertEqual(list_resources(), [])
                    with self.assertRaisesRegex(ValueError, "Unknown resource"):
                        read_resource("super-browser://README")
        finally:
            os.chdir(original_cwd)
            if old_root is None:
                os.environ.pop("SUPER_BROWSER_REPO_ROOT", None)
            else:
                os.environ["SUPER_BROWSER_REPO_ROOT"] = old_root

    def test_mcp_resources_reject_invalid_configured_root(self):
        old_root = os.environ.get("SUPER_BROWSER_REPO_ROOT")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                invalid_root = os.path.join(tmp, "invalid-super-browser-root")
                os.makedirs(os.path.join(invalid_root, "skills", "private-skill"))
                with open(os.path.join(invalid_root, "README.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Private Project\n")
                with open(os.path.join(invalid_root, "SKILL.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Private Skill\n")
                with open(os.path.join(invalid_root, "skills", "private-skill", "SKILL.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Private Nested Skill\n")

                os.environ["SUPER_BROWSER_REPO_ROOT"] = invalid_root
                self.assertEqual(list_resources(), [])
                with self.assertRaisesRegex(ValueError, "Unknown resource"):
                    read_resource("super-browser://README")
        finally:
            if old_root is None:
                os.environ.pop("SUPER_BROWSER_REPO_ROOT", None)
            else:
                os.environ["SUPER_BROWSER_REPO_ROOT"] = old_root

    def test_mcp_json_rpc_unknown_resource_is_protocol_error(self):
        stdin = io.StringIO(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "resources/read",
                    "params": {"uri": "super-browser://missing"},
                }
            )
            + "\n"
        )
        output = io.StringIO()
        with patch("sys.stdin", stdin), redirect_stdout(output):
            code = mcp_main()
        self.assertEqual(code, 0)
        response = json.loads(output.getvalue())
        self.assertIn("error", response)
        self.assertEqual(response["error"]["message"], "Unknown resource: super-browser://missing")

    def test_mcp_json_rpc_malformed_resource_read_envelopes_are_clear_protocol_errors(self):
        stdin = io.StringIO(
            "\n".join(
                [
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {}}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": []}),
                    json.dumps({"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "   "}}),
                    json.dumps({"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": 7}}),
                ]
            )
            + "\n"
        )
        output = io.StringIO()
        with patch("sys.stdin", stdin), redirect_stdout(output):
            code = mcp_main()
        self.assertEqual(code, 0)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        expected_messages = [
            "resources/read.uri must be a string",
            "resources/read params must be an object",
            "resources/read.uri must contain non-whitespace text",
            "resources/read.uri must be a string",
        ]
        self.assertEqual([response["id"] for response in responses], [1, 2, 3, 4])
        for response, expected_message in zip(responses, expected_messages):
            self.assertIn("error", response)
            self.assertEqual(response["error"]["message"], expected_message)

    def test_mcp_json_rpc_tool_errors_are_visible_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = os.path.join(tmp, "missing-state")
            os.environ["SUPER_BROWSER_STATE_DIR"] = state_dir
            stdin = io.StringIO(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "get_browser_run", "arguments": {"run_id": "run_missing"}},
                    }
                )
                + "\n"
            )
            output = io.StringIO()
            with patch("sys.stdin", stdin), redirect_stdout(output):
                code = mcp_main()
            self.assertEqual(code, 0)
            response = json.loads(output.getvalue())
            self.assertNotIn("error", response)
            result = response["result"]
            self.assertTrue(result["isError"])
            self.assertEqual(result["structuredContent"]["error_type"], "ValueError")
            self.assertIn("Run not found: run_missing", result["structuredContent"]["error"])
            self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
            self.assertFalse(os.path.exists(state_dir))

    def test_mcp_json_rpc_known_tool_exceptions_are_visible_redacted_results(self):
        stdin = io.StringIO(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "resume_browser_run", "arguments": {"run_id": "run_any"}},
                }
            )
            + "\n"
        )
        output = io.StringIO()
        with patch(
            "super_browser.mcp_server.resume_run",
            side_effect=RuntimeError("BROWSER_USE_API_KEY=super-secret-value"),
        ):
            with patch("sys.stdin", stdin), redirect_stdout(output):
                code = mcp_main()
        self.assertEqual(code, 0)
        response = json.loads(output.getvalue())
        self.assertNotIn("error", response)
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error_type"], "RuntimeError")
        self.assertIn("BROWSER_USE_API_KEY=[REDACTED]", result["structuredContent"]["error"])
        self.assertNotIn("super-secret-value", json.dumps(result))
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])

    def test_mcp_json_rpc_malformed_tool_call_envelopes_are_visible_results(self):
        stdin = io.StringIO(
            "\n".join(
                [
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": []}),
                    json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "   ", "arguments": {}}}),
                    json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "list_browser_runs", "arguments": []}}),
                ]
            )
            + "\n"
        )
        output = io.StringIO()
        with patch("sys.stdin", stdin), redirect_stdout(output):
            code = mcp_main()
        self.assertEqual(code, 0)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        expected_messages = [
            "tools/call.name must be a string",
            "tools/call params must be an object",
            "tools/call.name must contain non-whitespace text",
            "list_browser_runs arguments must be an object",
        ]
        self.assertEqual([response["id"] for response in responses], [1, 2, 3, 4])
        for response, expected_message in zip(responses, expected_messages):
            self.assertNotIn("error", response)
            result = response["result"]
            self.assertTrue(result["isError"])
            self.assertEqual(result["structuredContent"]["error_type"], "ValueError")
            self.assertEqual(result["structuredContent"]["error"], expected_message)
            self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])

    def test_mcp_json_rpc_unknown_tool_is_protocol_error(self):
        stdin = io.StringIO(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "unknown_tool", "arguments": {}},
                }
            )
            + "\n"
        )
        output = io.StringIO()
        with patch("sys.stdin", stdin), redirect_stdout(output):
            code = mcp_main()
        self.assertEqual(code, 0)
        response = json.loads(output.getvalue())
        self.assertIn("error", response)
        self.assertEqual(response["error"]["message"], "Unknown tool: unknown_tool")

    def test_mcp_json_rpc_malformed_request_does_not_reuse_previous_id(self):
        stdin = io.StringIO(
            "\n".join(
                [
                    json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}),
                    "{not-json",
                    json.dumps(["not", "an", "object"]),
                ]
            )
            + "\n"
        )
        output = io.StringIO()
        with patch("sys.stdin", stdin), redirect_stdout(output):
            code = mcp_main()
        self.assertEqual(code, 0)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(responses[0]["id"], 7)
        self.assertIn("result", responses[0])
        self.assertIsNone(responses[1]["id"])
        self.assertIn("error", responses[1])
        self.assertIsNone(responses[2]["id"])
        self.assertEqual(responses[2]["error"]["message"], "JSON-RPC request must be an object")

    def test_mcp_tool_validation_rejects_bad_arguments(self):
        with self.assertRaisesRegex(ValueError, "missing required argument: goal"):
            handle_tool("plan_browser_task", {})
        with self.assertRaisesRegex(ValueError, "plan_browser_task.goal must be at least 1 character"):
            handle_tool("plan_browser_task", {"goal": ""})
        with self.assertRaisesRegex(ValueError, "plan_browser_task.goal must contain non-whitespace text"):
            handle_tool("plan_browser_task", {"goal": "   "})
        with self.assertRaisesRegex(ValueError, "providers_allowed\\[0\\] must be one of"):
            handle_tool("plan_browser_task", {"goal": "Extract", "providers_allowed": ["unknown-provider"]})
        with self.assertRaisesRegex(ValueError, "url must not contain username or password"):
            handle_tool("plan_browser_task", {"goal": "Extract", "url": "https://agent:secret@example.com/private"})
        with self.assertRaisesRegex(ValueError, "plan_browser_task.max_cost_usd must be finite"):
            handle_tool("plan_browser_task", {"goal": "Extract", "max_cost_usd": math.inf})
        with self.assertRaisesRegex(ValueError, "plan_browser_task.timeout_seconds must be >= 1"):
            handle_tool("plan_browser_task", {"goal": "Extract", "timeout_seconds": 0})
        with self.assertRaisesRegex(ValueError, "plan_browser_task.timeout_seconds must be an integer"):
            handle_tool("plan_browser_task", {"goal": "Extract", "timeout_seconds": 1.5})
        with self.assertRaisesRegex(ValueError, "approve_browser_run missing required argument: reason"):
            handle_tool("approve_browser_run", {"run_id": "run_missing"})
        with self.assertRaisesRegex(ValueError, "approve_browser_run.reason must be at least 1 character"):
            handle_tool("approve_browser_run", {"run_id": "run_missing", "reason": ""})
        with self.assertRaisesRegex(ValueError, "approve_browser_run.reason must contain non-whitespace text"):
            handle_tool("approve_browser_run", {"run_id": "run_missing", "reason": "   "})
        with self.assertRaisesRegex(ValueError, "resume_browser_run.run_id must contain non-whitespace text"):
            handle_tool("resume_browser_run", {"run_id": "   "})
        with self.assertRaisesRegex(ValueError, "deny_browser_run missing required argument: reason"):
            handle_tool("deny_browser_run", {"run_id": "run_missing"})
        with self.assertRaisesRegex(ValueError, "unsupported argument: extra"):
            handle_tool("browser_doctor", {"extra": True})
        with self.assertRaisesRegex(ValueError, "install_super_browser_skill.name must be at least 1 character"):
            handle_tool("install_super_browser_skill", {"name": ""})
        with self.assertRaisesRegex(ValueError, "init_super_browser_mcp.cwd must contain non-whitespace text"):
            handle_tool("init_super_browser_mcp", {"cwd": "   "})
        with self.assertRaisesRegex(ValueError, "init_super_browser_mcp.force must be a boolean"):
            handle_tool("init_super_browser_mcp", {"force": "yes"})
        with self.assertRaisesRegex(ValueError, "list_browser_runs.limit must be >= 1"):
            handle_tool("list_browser_runs", {"limit": 0})
        with self.assertRaisesRegex(ValueError, "list_browser_runs.limit must be an integer"):
            handle_tool("list_browser_runs", {"limit": 1.5})

    def test_mcp_plan_honors_provider_constraints(self):
        payload = handle_tool(
            "plan_browser_task",
            {"goal": "Extract titles from https://example.com", "providers_allowed": ["steel"], "max_cost_usd": 0.05, "timeout_seconds": 12},
        )
        self.assertEqual(payload["primary_provider"], "steel")
        self.assertEqual(payload["fallback_providers"], [])
        self.assertEqual(payload["council_report"]["planner_decision"]["max_cost_usd"], 0.05)
        self.assertEqual(payload["council_report"]["planner_decision"]["timeout_seconds"], 12)

    def test_mcp_provider_tool(self):
        payload = handle_tool("list_browser_providers", {})
        names = {provider["name"] for provider in payload}
        self.assertIn("browser-use", names)
        self.assertIn("hyperbrowser", names)

    def test_mcp_doctor_exposes_readiness_status_fields(self):
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                payload = handle_tool("browser_doctor", {})
                providers = {provider["name"]: provider for provider in payload["providers"]}
                decodo = providers["decodo-http"]
                self.assertIn("readiness_status", decodo)
                self.assertIn("usable_now", decodo)
                self.assertIn("production_ready", decodo)
                self.assertIn("production_ready_scope", decodo)
                self.assertIn("certified_workflow_classes", decodo)
                self.assertIn("stale_certified_workflow_classes", decodo)
                self.assertIn("supported_live_workflow_classes", decodo)
                self.assertIn("uncertified_workflow_classes", decodo)
                self.assertIn("ignored_unsupported_evidence_workflow_classes", decodo)
                self.assertIn("ignored_provider_mismatch_evidence_workflow_classes", decodo)
                self.assertIn("requires_live_test_before_production", decodo)
                self.assertIn("requires_live_test_before_broader_production", decodo)
                self.assertIn("production_blockers", decodo)
                self.assertIn("latest_live_test", decodo)
                self.assertEqual(decodo["readiness_status"], "usable_direct_http_no_proxy")
                self.assertTrue(decodo["usable_now"])
                self.assertFalse(decodo["production_ready"])
                self.assertEqual(decodo["production_ready_scope"], "none")
                self.assertEqual(decodo["certified_workflow_classes"], [])
                self.assertEqual(decodo["stale_certified_workflow_classes"], [])
                self.assertEqual(decodo["uncertified_workflow_classes"], ["raw_http_direct"])
                self.assertEqual(decodo["ignored_unsupported_evidence_workflow_classes"], [])
                self.assertEqual(decodo["ignored_provider_mismatch_evidence_workflow_classes"], [])
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_mcp_doctor_ignores_provider_mismatched_live_test_evidence(self):
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                evidence_dir = os.path.join(tmp, "live-tests")
                os.makedirs(evidence_dir)
                recorded_at = utc_now()
                with open(os.path.join(evidence_dir, "decodo-http.json"), "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "provider": "browser-use",
                            "requested_provider": "browser-use",
                            "status": "passed",
                            "recorded_at": recorded_at,
                            "workflow_class": "raw_http_direct",
                            "certification_scope": "workflow_class",
                            "certified_workflow_classes": ["raw_http_direct"],
                            "latest_by_workflow_class": {
                                "raw_http_direct": {
                                    "provider": "browser-use",
                                    "requested_provider": "browser-use",
                                    "status": "passed",
                                    "recorded_at": recorded_at,
                                    "workflow_class": "raw_http_direct",
                                    "certification_scope": "workflow_class",
                                    "certified_workflow_classes": ["raw_http_direct"],
                                }
                            },
                        },
                        handle,
                    )

                doctor = handle_tool("browser_doctor", {})
                providers = {provider["name"]: provider for provider in doctor["providers"]}
                decodo = providers["decodo-http"]
                self.assertEqual(decodo["readiness_status"], "usable_direct_http_no_proxy")
                self.assertFalse(decodo["production_ready"])
                self.assertEqual(decodo["production_ready_scope"], "none")
                self.assertEqual(decodo["certified_workflow_classes"], [])
                self.assertEqual(decodo["stale_certified_workflow_classes"], [])
                self.assertEqual(decodo["ignored_provider_mismatch_evidence_workflow_classes"], ["raw_http_direct"])
                self.assertEqual(decodo["ignored_unsupported_evidence_workflow_classes"], [])
                self.assertEqual(decodo["uncertified_workflow_classes"], ["raw_http_direct"])
                self.assertTrue(any("provider-mismatched live-test evidence" in blocker for blocker in decodo["production_blockers"]))
                self.assertTrue(any("raw_http_direct" in blocker for blocker in decodo["production_blockers"]))
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_mcp_doctor_filters_mixed_provider_identity_evidence(self):
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                evidence_dir = os.path.join(tmp, "live-tests")
                os.makedirs(evidence_dir)
                recorded_at = utc_now()
                with open(os.path.join(evidence_dir, "decodo-http.json"), "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "provider": "decodo-http",
                            "requested_provider": "all",
                            "status": "passed",
                            "recorded_at": recorded_at,
                            "workflow_class": "raw_http_direct",
                            "certification_scope": "workflow_class",
                            "certified_workflow_classes": ["raw_http_direct"],
                            "latest_by_workflow_class": {
                                "raw_http_direct": {
                                    "provider": "decodo-http",
                                    "requested_provider": "all",
                                    "status": "passed",
                                    "recorded_at": recorded_at,
                                    "workflow_class": "raw_http_direct",
                                    "certification_scope": "workflow_class",
                                    "certified_workflow_classes": ["raw_http_direct"],
                                },
                            },
                        },
                        handle,
                    )

                doctor = handle_tool("browser_doctor", {})
                providers = {provider["name"]: provider for provider in doctor["providers"]}
                decodo = providers["decodo-http"]
                self.assertEqual(decodo["readiness_status"], "live_test_passed")
                self.assertTrue(decodo["production_ready"])
                self.assertEqual(decodo["production_ready_scope"], "workflow_class:raw_http_direct")
                self.assertEqual(decodo["certified_workflow_classes"], ["raw_http_direct"])
                self.assertEqual(decodo["stale_certified_workflow_classes"], [])
                self.assertEqual(decodo["ignored_provider_mismatch_evidence_workflow_classes"], [])
                self.assertEqual(decodo["ignored_unsupported_evidence_workflow_classes"], [])
                self.assertEqual(decodo["uncertified_workflow_classes"], [])
                self.assertFalse(decodo["requires_live_test_before_production"])
                self.assertFalse(decodo["requires_live_test_before_broader_production"])
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_mcp_doctor_ignores_unsupported_live_test_workflow_evidence(self):
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                evidence_dir = os.path.join(tmp, "live-tests")
                os.makedirs(evidence_dir)
                recorded_at = utc_now()
                with open(os.path.join(evidence_dir, "decodo-http.json"), "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "provider": "decodo-http",
                            "requested_provider": "decodo-http",
                            "status": "passed",
                            "recorded_at": recorded_at,
                            "workflow_class": "general_read",
                            "certification_scope": "workflow_class",
                            "certified_workflow_classes": ["general_read"],
                            "latest_by_workflow_class": {
                                "general_read": {
                                    "provider": "decodo-http",
                                    "requested_provider": "decodo-http",
                                    "status": "passed",
                                    "recorded_at": recorded_at,
                                    "workflow_class": "general_read",
                                    "certification_scope": "workflow_class",
                                    "certified_workflow_classes": ["general_read"],
                                }
                            },
                        },
                        handle,
                    )

                doctor = handle_tool("browser_doctor", {})
                providers = {provider["name"]: provider for provider in doctor["providers"]}
                decodo = providers["decodo-http"]
                self.assertEqual(decodo["readiness_status"], "usable_direct_http_no_proxy")
                self.assertFalse(decodo["production_ready"])
                self.assertEqual(decodo["production_ready_scope"], "none")
                self.assertEqual(decodo["certified_workflow_classes"], [])
                self.assertEqual(decodo["stale_certified_workflow_classes"], [])
                self.assertEqual(decodo["ignored_unsupported_evidence_workflow_classes"], ["general_read"])
                self.assertEqual(decodo["uncertified_workflow_classes"], ["raw_http_direct"])
                self.assertTrue(any("unsupported live-test evidence" in blocker for blocker in decodo["production_blockers"]))
                self.assertTrue(any("raw_http_direct" in blocker for blocker in decodo["production_blockers"]))
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_mcp_doctor_filters_mixed_supported_and_unsupported_workflow_evidence(self):
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                evidence_dir = os.path.join(tmp, "live-tests")
                os.makedirs(evidence_dir)
                recorded_at = utc_now()
                with open(os.path.join(evidence_dir, "decodo-http.json"), "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "provider": "decodo-http",
                            "requested_provider": "decodo-http",
                            "status": "passed",
                            "recorded_at": recorded_at,
                            "workflow_class": "raw_http_direct",
                            "certification_scope": "workflow_class",
                            "certified_workflow_classes": ["general_read", "raw_http_direct"],
                            "latest_by_workflow_class": {
                                "general_read": {
                                    "provider": "decodo-http",
                                    "requested_provider": "decodo-http",
                                    "status": "passed",
                                    "recorded_at": recorded_at,
                                    "workflow_class": "general_read",
                                    "certification_scope": "workflow_class",
                                    "certified_workflow_classes": ["general_read"],
                                },
                                "raw_http_direct": {
                                    "provider": "decodo-http",
                                    "requested_provider": "decodo-http",
                                    "status": "passed",
                                    "recorded_at": recorded_at,
                                    "workflow_class": "raw_http_direct",
                                    "certification_scope": "workflow_class",
                                    "certified_workflow_classes": ["raw_http_direct"],
                                },
                            },
                        },
                        handle,
                    )

                doctor = handle_tool("browser_doctor", {})
                providers = {provider["name"]: provider for provider in doctor["providers"]}
                decodo = providers["decodo-http"]
                self.assertEqual(decodo["readiness_status"], "live_test_passed")
                self.assertTrue(decodo["production_ready"])
                self.assertEqual(decodo["production_ready_scope"], "workflow_class:raw_http_direct")
                self.assertEqual(decodo["certified_workflow_classes"], ["raw_http_direct"])
                self.assertEqual(decodo["stale_certified_workflow_classes"], [])
                self.assertEqual(decodo["ignored_unsupported_evidence_workflow_classes"], ["general_read"])
                self.assertEqual(decodo["uncertified_workflow_classes"], [])
                self.assertFalse(decodo["requires_live_test_before_production"])
                self.assertFalse(decodo["requires_live_test_before_broader_production"])
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_mcp_live_test_evidence_promotes_verified_provider_in_doctor(self):
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                live_test = handle_tool("run_browser_live_tests", {"provider": "decodo-http"})
                self.assertEqual(live_test["status"], "passed")
                self.assertTrue(live_test["evidence"]["recorded"])
                self.assertEqual(live_test["evidence"]["written"][0]["provider"], "decodo-http")

                doctor = handle_tool("browser_doctor", {})
                providers = {provider["name"]: provider for provider in doctor["providers"]}
                decodo = providers["decodo-http"]
                self.assertEqual(decodo["readiness_status"], "live_test_passed")
                self.assertTrue(decodo["usable_now"])
                self.assertTrue(decodo["production_ready"])
                self.assertEqual(decodo["production_ready_scope"], "workflow_class:raw_http_direct")
                self.assertEqual(decodo["certified_workflow_classes"], ["raw_http_direct"])
                self.assertEqual(decodo["latest_live_test"]["status"], "passed")
                self.assertTrue(decodo["latest_live_test"]["fresh"])
                self.assertEqual(decodo["latest_live_test"]["workflow_class"], "raw_http_direct")
                self.assertEqual(decodo["latest_live_test"]["certification_scope"], "workflow_class")
                self.assertEqual(decodo["latest_live_test"]["certified_workflow_classes"], ["raw_http_direct"])
                self.assertEqual(decodo["supported_live_workflow_classes"], ["raw_http_direct"])
                self.assertEqual(decodo["uncertified_workflow_classes"], [])
                self.assertFalse(decodo["requires_live_test_before_production"])
                self.assertFalse(decodo["requires_live_test_before_broader_production"])
                self.assertIn("DECODO_PROXY", decodo["missing_optional_env"])
                self.assertIn("raw_http_direct", decodo["production_gate"])
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_mcp_live_test_evidence_accumulates_by_workflow_class(self):
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                raw_http = handle_tool("run_browser_live_tests", {"provider": "decodo-http", "workflow_class": "raw_http_direct"})
                self.assertEqual(raw_http["status"], "passed")
                self.assertEqual(raw_http["evidence"]["written"][0]["certified_workflow_classes"], ["raw_http_direct"])

                write_gate = handle_tool("run_browser_live_tests", {"provider": "decodo-http", "workflow_class": "external_write_gate"})
                self.assertEqual(write_gate["status"], "failed")
                self.assertTrue(write_gate["results"][0]["unsupported_workflow_class"])

                evidence_path = os.path.join(tmp, "live-tests", "decodo-http.json")
                with open(evidence_path, encoding="utf-8") as handle:
                    evidence = json.load(handle)
                self.assertEqual(list(evidence["latest_by_workflow_class"]), ["raw_http_direct"])

                doctor = handle_tool("browser_doctor", {})
                providers = {provider["name"]: provider for provider in doctor["providers"]}
                decodo = providers["decodo-http"]
                self.assertEqual(decodo["readiness_status"], "live_test_passed")
                self.assertEqual(decodo["production_ready_scope"], "workflow_class:raw_http_direct")
                self.assertEqual(decodo["certified_workflow_classes"], ["raw_http_direct"])
                self.assertEqual(decodo["uncertified_workflow_classes"], [])
                self.assertFalse(decodo["requires_live_test_before_broader_production"])
                self.assertEqual(decodo["latest_live_test"]["workflow_class"], "raw_http_direct")
                self.assertEqual(decodo["latest_live_test"]["certified_workflow_classes"], ["raw_http_direct"])
                by_class = decodo["latest_live_test"]["latest_by_workflow_class"]
                self.assertEqual(by_class["raw_http_direct"]["status"], "passed")
                self.assertTrue(by_class["raw_http_direct"]["fresh"])
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_live_test_evidence_loads_legacy_single_workflow_record(self):
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                evidence_dir = os.path.join(tmp, "live-tests")
                os.makedirs(evidence_dir)
                with open(os.path.join(evidence_dir, "decodo-http.json"), "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "provider": "decodo-http",
                            "requested_provider": "decodo-http",
                            "status": "passed",
                            "recorded_at": utc_now(),
                            "workflow_class": "raw_http_direct",
                            "certification_scope": "workflow_class",
                            "certified_workflow_classes": ["raw_http_direct"],
                        },
                        handle,
                    )

                evidence = load_live_test_evidence("decodo-http")
                self.assertIsNotNone(evidence)
                self.assertTrue(evidence["fresh"])
                self.assertEqual(evidence["certified_workflow_classes"], ["raw_http_direct"])
                self.assertEqual(evidence["latest_by_workflow_class"]["raw_http_direct"]["status"], "passed")

                doctor = handle_tool("browser_doctor", {})
                providers = {provider["name"]: provider for provider in doctor["providers"]}
                decodo = providers["decodo-http"]
                self.assertEqual(decodo["readiness_status"], "live_test_passed")
                self.assertEqual(decodo["production_ready_scope"], "workflow_class:raw_http_direct")
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_live_test_skipped_result_does_not_erase_existing_workflow_proof(self):
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                passed = {
                    "results": [
                        {
                            "provider": "browser-use",
                            "status": "passed",
                            "workflow_class": "general_read",
                            "run_id": "run_passed",
                            "verification": {"confidence": "high", "checks": ["provider completed"]},
                        }
                    ]
                }
                skipped = {
                    "results": [
                        {
                            "provider": "browser-use",
                            "status": "skipped",
                            "workflow_class": "general_read",
                            "missing_env": ["BROWSER_USE_API_KEY"],
                            "reason": "live provider credentials are not configured",
                        }
                    ]
                }

                record_live_test_evidence(passed, "browser-use", {"browser-use"})
                record_live_test_evidence(skipped, "browser-use", {"browser-use"})
                evidence = load_live_test_evidence("browser-use")

                self.assertEqual(evidence["status"], "skipped")
                self.assertEqual(evidence["workflow_class"], "general_read")
                self.assertEqual(evidence["certified_workflow_classes"], ["general_read"])
                self.assertEqual(evidence["latest_by_workflow_class"]["general_read"]["status"], "passed")
        finally:
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_live_test_failed_result_invalidates_existing_workflow_proof(self):
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                passed = {
                    "results": [
                        {
                            "provider": "browser-use",
                            "status": "passed",
                            "workflow_class": "general_read",
                            "run_id": "run_passed",
                            "verification": {"confidence": "high", "checks": ["provider completed"]},
                        }
                    ]
                }
                failed = {
                    "results": [
                        {
                            "provider": "browser-use",
                            "status": "failed",
                            "workflow_class": "general_read",
                            "run_id": "run_failed",
                            "error": "provider live test failed",
                        }
                    ]
                }

                record_live_test_evidence(passed, "browser-use", {"browser-use"})
                record_live_test_evidence(failed, "browser-use", {"browser-use"})
                evidence = load_live_test_evidence("browser-use")

                self.assertEqual(evidence["status"], "failed")
                self.assertEqual(evidence["certified_workflow_classes"], [])
                self.assertEqual(evidence["latest_by_workflow_class"]["general_read"]["status"], "failed")
        finally:
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_mcp_list_browser_runs_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False)
            create_run("Post this comment on LinkedIn", execute=False)
            payload = handle_tool("list_browser_runs", {"status": "planned", "limit": 1})
            listed = {item["run_id"]: item for item in payload}
            self.assertIn(run.run_id, listed)
            self.assertEqual(listed[run.run_id]["status"], "planned")
            self.assertEqual(listed[run.run_id]["event_count"], 1)
            self.assertNotIn("events", listed[run.run_id])
            details = handle_tool("list_browser_runs", {"status": "planned", "include_details": True})
            detailed = {item["run_id"]: item for item in details}
            self.assertEqual([event["type"] for event in detailed[run.run_id]["events"]], ["run_created"])
            stored = RunStore().get(run.run_id)
            self.assertEqual([event["type"] for event in stored["events"]], ["run_created"])

    def test_mcp_handoff_browser_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Extract titles from https://example.com", execute=False, timeout_seconds=33)
            payload = handle_tool("handoff_browser_run", {"run_id": run.run_id})

            self.assertEqual(payload["type"], "super_browser_run_handoff")
            self.assertEqual(payload["run_id"], run.run_id)
            self.assertEqual(payload["summary"]["event_count"], 1)
            self.assertEqual(payload["task"]["target_scope"], "public_web")
            self.assertEqual(payload["task"]["timeout_seconds"], 33)
            self.assertEqual(payload["route"]["timeout_seconds"], 33)
            self.assertTrue(payload["resume"]["safe_to_resume"])
            self.assertEqual(payload["verification"]["status"], "planned")
            self.assertEqual(payload["verification"]["artifact_count"], 1)
            self.assertEqual(payload["verification"]["policy_guard"]["approval_status"], "not_required")
            self.assertEqual(payload["verification"]["plan_integrity"]["status"], "unavailable")
            self.assertIn("super-browser://references/routing-playbook", payload["docs"])
            readiness = payload["provider_readiness"][0]
            self.assertIn("production_ready_scope", readiness)
            self.assertIn("certified_workflow_classes", readiness)
            self.assertIn("uncertified_workflow_classes", readiness)
            self.assertIn("production_blockers", readiness)

            stored = RunStore().get(run.run_id)
            self.assertEqual([event["type"] for event in stored["events"]], ["run_created"])

    def test_mcp_live_test_tool(self):
        payload = handle_tool("run_browser_live_tests", {"provider": "decodo-http", "workflow_class": "raw_http_direct"})
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["results"][0]["provider"], "decodo-http")
        self.assertEqual(payload["results"][0]["workflow_class"], "raw_http_direct")

    def test_cli_live_test_accepts_workflow_class(self):
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                output = io.StringIO()
                with redirect_stdout(output):
                    code = cli_main(["live-test", "--provider", "decodo-http", "--workflow-class", "raw_http_direct"])
                self.assertEqual(code, 0)
                payload = json.loads(output.getvalue())
                self.assertEqual(payload["status"], "passed")
                self.assertEqual(payload["results"][0]["workflow_class"], "raw_http_direct")
        finally:
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_live_test_rejects_unsupported_workflow_class_without_recording_evidence(self):
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                payload = handle_tool("run_browser_live_tests", {"provider": "decodo-http", "workflow_class": "general_read"})
                self.assertEqual(payload["status"], "failed")
                self.assertFalse(payload["evidence"]["recorded"])
                self.assertEqual(payload["evidence"]["reason"], "unsupported_workflow_class")
                self.assertEqual(payload["results"][0]["workflow_class"], "general_read")
                self.assertEqual(payload["results"][0]["supported_workflow_classes"], ["raw_http_direct"])
                self.assertTrue(payload["results"][0]["unsupported_workflow_class"])
                self.assertFalse(os.path.exists(os.path.join(tmp, "live-tests")))
        finally:
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_external_write_gate_live_test_stops_before_provider_execution_without_keys(self):
        old_key = os.environ.pop("BROWSER_USE_API_KEY", None)
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                payload = handle_tool("run_browser_live_tests", {"provider": "browser-use", "workflow_class": "external_write_gate"})
                self.assertEqual(payload["status"], "passed")
                result = payload["results"][0]
                self.assertEqual(result["provider"], "browser-use")
                self.assertEqual(result["workflow_class"], "external_write_gate")
                self.assertEqual(result["selected_provider"], "browser-use")
                self.assertEqual(result["approvals"][0]["status"], "pending")
                self.assertNotIn("execution_started", [event["type"] for event in result["events"]])
                self.assertIn("provider execution did not start", result["verification"]["checks"])
                self.assertTrue(payload["evidence"]["recorded"])
                self.assertEqual(payload["evidence"]["written"][0]["provider"], "browser-use")

                doctor = handle_tool("browser_doctor", {})
                providers = {provider["name"]: provider for provider in doctor["providers"]}
                browser_use = providers["browser-use"]
                self.assertEqual(browser_use["readiness_status"], "missing_env")
                self.assertFalse(browser_use["production_ready"])
                self.assertEqual(browser_use["production_ready_scope"], "none")
                self.assertEqual(browser_use["latest_live_test"]["workflow_class"], "external_write_gate")
                self.assertEqual(browser_use["latest_live_test"]["certified_workflow_classes"], ["external_write_gate"])
        finally:
            if old_key is not None:
                os.environ["BROWSER_USE_API_KEY"] = old_key
            if old_state is None:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            else:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state

    def test_mcp_live_test_missing_provider_key_skips(self):
        old_key = os.environ.pop("HYPERBROWSER_API_KEY", None)
        try:
            payload = handle_tool("run_browser_live_tests", {"provider": "hyperbrowser", "workflow_class": "general_read"})
            self.assertEqual(payload["status"], "skipped")
            self.assertEqual(payload["results"][0]["provider"], "hyperbrowser")
            self.assertEqual(payload["results"][0]["workflow_class"], "general_read")
            self.assertEqual(payload["results"][0]["missing_env"], ["HYPERBROWSER_API_KEY"])
        finally:
            if old_key is not None:
                os.environ["HYPERBROWSER_API_KEY"] = old_key

    def test_provider_live_test_uses_runtime_lifecycle_for_read_only_provider(self):
        old_key = os.environ.get("HYPERBROWSER_API_KEY")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                os.environ["HYPERBROWSER_API_KEY"] = "test-key"
                plan = build_plan(infer_task("Read https://example.com and return the page title plus a short summary", url="https://example.com", providers_allowed=["hyperbrowser"]))
                planned = RunState.create(plan, status="planned")
                complete = RunState.create(plan, status="complete")
                complete.run_id = planned.run_id
                complete.verification = {"selected_provider": "hyperbrowser", "checks": ["runtime lifecycle"]}

                with patch("super_browser.live_tests.create_run", return_value=planned) as create_mock:
                    with patch("super_browser.live_tests.resume_run", return_value=complete) as resume_mock:
                        with patch("super_browser.live_tests.approve_run") as approve_mock:
                            payload = handle_tool("run_browser_live_tests", {"provider": "hyperbrowser"})

                self.assertEqual(payload["status"], "passed")
                self.assertEqual(payload["results"][0]["run_id"], planned.run_id)
                self.assertEqual(payload["results"][0]["selected_provider"], "hyperbrowser")
                create_mock.assert_called_once()
                self.assertEqual(create_mock.call_args.kwargs["providers_allowed"], ["hyperbrowser"])
                self.assertFalse(create_mock.call_args.kwargs["execute"])
                resume_mock.assert_called_once_with(planned.run_id)
                approve_mock.assert_not_called()
        finally:
            if old_key is not None:
                os.environ["HYPERBROWSER_API_KEY"] = old_key
            else:
                os.environ.pop("HYPERBROWSER_API_KEY", None)

    def test_provider_live_test_approves_policy_gated_provider_through_runtime(self):
        old_key = os.environ.get("BROWSER_USE_API_KEY")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                os.environ["BROWSER_USE_API_KEY"] = "test-key"
                plan = build_plan(infer_task("Use the available browser session to read https://example.com and report the page title", url="https://example.com", providers_allowed=["browser-use"]))
                planned = RunState.create(plan, status="awaiting_approval")
                planned.approvals.append(approval_request_from_plan(plan))
                complete = RunState.create(plan, status="complete")
                complete.run_id = planned.run_id
                complete.verification = {"selected_provider": "browser-use", "checks": ["approved runtime lifecycle"]}
                complete.approvals = planned.approvals

                with patch("super_browser.live_tests.create_run", return_value=planned) as create_mock:
                    with patch("super_browser.live_tests.approve_run", return_value=complete) as approve_mock:
                        with patch("super_browser.live_tests.resume_run") as resume_mock:
                            payload = handle_tool("run_browser_live_tests", {"provider": "browser-use"})

                self.assertEqual(payload["status"], "passed")
                create_mock.assert_called_once()
                self.assertEqual(create_mock.call_args.kwargs["providers_allowed"], ["browser-use"])
                self.assertFalse(create_mock.call_args.kwargs["execute"])
                approve_mock.assert_called_once()
                self.assertEqual(approve_mock.call_args.args[0], planned.run_id)
                self.assertTrue(approve_mock.call_args.kwargs["execute"])
                self.assertEqual(approve_mock.call_args.kwargs["approver"], "super-browser-live-test")
                resume_mock.assert_not_called()
        finally:
            if old_key is not None:
                os.environ["BROWSER_USE_API_KEY"] = old_key
            else:
                os.environ.pop("BROWSER_USE_API_KEY", None)

    def test_mcp_fixture_live_tests_cover_required_scenarios(self):
        payload = handle_tool("run_browser_live_tests", {"provider": "fixtures"})
        self.assertEqual(payload["status"], "passed")
        scenarios = {item.get("scenario") for item in payload["results"]}
        self.assertEqual(
            scenarios,
            {
                "login",
                "infinite_scroll",
                "form_fill_no_submit",
                "social_feed_comment_draft",
                "lead_generation_local_artifact",
                "modal_handling",
                "file_upload",
                "blocked_page",
                "long_running_resume",
                "stale_long_running_resume",
            },
        )
        social_draft = [item for item in payload["results"] if item.get("scenario") == "social_feed_comment_draft"][0]
        self.assertEqual(social_draft["matched_posts"], 2)
        self.assertEqual(social_draft["draft"], "Fixture comment draft for high-intent roofing lead")
        self.assertEqual(social_draft["published"], "false")
        lead_export = [item for item in payload["results"] if item.get("scenario") == "lead_generation_local_artifact"][0]
        self.assertEqual(lead_export["qualified_leads"], 2)
        self.assertEqual(lead_export["lead_names"], ["Avery Roofing", "Northstar Solar"])
        self.assertEqual(lead_export["crm_synced"], "false")
        self.assertEqual(lead_export["emailed"], "false")
        stale_resume = [item for item in payload["results"] if item.get("scenario") == "stale_long_running_resume"][0]
        self.assertIn("stale execution recovered", stale_resume["checks"])

    def test_mcp_resume_executes_planned_run_and_get_is_read_only(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                url = f"http://127.0.0.1:{server.server_port}/data.json"
                run = create_run("Fetch this JSON endpoint through raw HTTP", url=url, execute=False)
                fetched = handle_tool("get_browser_run", {"run_id": run.run_id})
                self.assertEqual(fetched["status"], "planned")
                resumed = handle_tool("resume_browser_run", {"run_id": run.run_id})
                self.assertEqual(resumed["status"], "complete")
                self.assertEqual(resumed["verification"]["selected_provider"], "decodo-http")
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            server.shutdown()
            server.server_close()

    def test_mcp_verify_uses_active_verifier(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                url = f"http://127.0.0.1:{server.server_port}/data.json"
                run = create_run("Fetch this JSON endpoint through raw HTTP", url=url)
                report = handle_tool("verify_browser_run", {"run_id": run.run_id})
                self.assertEqual(report["status"], "complete")
                self.assertEqual(report["selected_provider"], "decodo-http")
                self.assertEqual(report["failures"], [])
                self.assertIn("run-report.json parsed", report["checks"])
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            server.shutdown()
            server.server_close()

    def test_cli_verify_uses_active_verifier(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old_proxy = os.environ.pop("DECODO_PROXY", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
                url = f"http://127.0.0.1:{server.server_port}/data.json"
                run = create_run("Fetch this JSON endpoint through raw HTTP", url=url)
                output = io.StringIO()
                with redirect_stdout(output):
                    code = cli_main(["verify", run.run_id])
                self.assertEqual(code, 0)
                report = json.loads(output.getvalue())
                self.assertEqual(report["selected_provider"], "decodo-http")
                self.assertEqual(report["failures"], [])
                self.assertTrue(report["verification_report_path"].endswith("verification-report.json"))
        finally:
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy
            server.shutdown()
            server.server_close()

    def test_cli_approve_and_deny_commands_are_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["run", "--goal", "Post this comment"])
            self.assertEqual(code, 0)
            run_id = json.loads(output.getvalue())["run_id"]
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli_main(["approve", run_id, "--by", "cli-test", "--reason", "approved"])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "approved")

    def test_cli_approve_requires_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Post this comment")
            error_output = io.StringIO()
            with self.assertRaises(SystemExit) as raised:
                with redirect_stderr(error_output):
                    cli_main(["approve", run.run_id])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("--reason", error_output.getvalue())

    def test_mcp_and_cli_approval_reject_blank_actor(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Post this comment")

            with self.assertRaisesRegex(ValueError, "approve_browser_run.by must contain non-whitespace text"):
                handle_tool("approve_browser_run", {"run_id": run.run_id, "by": " ", "reason": "approved"})

            output = io.StringIO()
            error_output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(error_output):
                code = cli_main(["approve", run.run_id, "--by", " ", "--reason", "approved"])

            self.assertEqual(code, 1)
            self.assertIn("approval actor is required", error_output.getvalue())


if __name__ == "__main__":
    unittest.main()
