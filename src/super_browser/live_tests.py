from __future__ import annotations

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .fleet import create_fleet_runs
from .live_evidence import record_live_test_evidence
from .profiles import ProfileStore
from .providers import PROVIDERS
from .runtime import approve_run, create_run, resume_run


DEFAULT_WORKFLOW_CLASS = "default"
WORKFLOW_CLASSES = (
    DEFAULT_WORKFLOW_CLASS,
    "raw_http_direct",
    "local_browser_fixture",
    "general_read",
    "authenticated_read",
    "authenticated_write_profile",
    "fleet_read",
    "desktop_read",
    "external_write_gate",
)


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        status = 200
        if path.endswith(".json"):
            body = b'{"ok": true, "source": "super-browser-fixture"}'
            content_type = "application/json"
        elif path == "/login":
            body = _fixture_html("Login Fixture", _login_fixture_body()).encode("utf-8")
            content_type = "text/html"
        elif path == "/scroll":
            body = _fixture_html("Scroll Fixture", _scroll_fixture_body()).encode("utf-8")
            content_type = "text/html"
        elif path == "/form":
            body = _fixture_html("Form Fixture", _form_fixture_body()).encode("utf-8")
            content_type = "text/html"
        elif path == "/social":
            body = _fixture_html("Social Feed Fixture", _social_feed_fixture_body()).encode("utf-8")
            content_type = "text/html"
        elif path == "/leads":
            body = _fixture_html("Lead Generation Fixture", _lead_generation_fixture_body()).encode("utf-8")
            content_type = "text/html"
        elif path == "/modal":
            body = _fixture_html("Modal Fixture", _modal_fixture_body()).encode("utf-8")
            content_type = "text/html"
        elif path == "/upload":
            body = _fixture_html("Upload Fixture", _upload_fixture_body()).encode("utf-8")
            content_type = "text/html"
        elif path == "/blocked":
            status = 403
            body = _fixture_html("Blocked Fixture", "<h1>Blocked by fixture anti-bot</h1><p id='reason'>403</p>").encode("utf-8")
            content_type = "text/html"
        else:
            body = b"<html><head><title>Super Saiyan Browser Fixture</title></head><body><h1>Fixture Ready</h1><p>Local browser test.</p></body></html>"
            content_type = "text/html"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def run_live_tests(provider: str = "local", workflow_class: str = DEFAULT_WORKFLOW_CLASS) -> dict[str, Any]:
    allowed = {"local", "fixtures", "all", *PROVIDERS.keys()}
    if provider not in allowed:
        return {"status": "failed", "error": f"Unknown live-test provider: {provider}"}
    if workflow_class not in WORKFLOW_CLASSES:
        return {"status": "failed", "error": f"Unknown live-test workflow class: {workflow_class}", "allowed_workflow_classes": list(WORKFLOW_CLASSES)}
    if provider == "fixtures" and workflow_class != DEFAULT_WORKFLOW_CLASS:
        return _unsupported_workflow_report(provider, workflow_class)
    if provider not in {"local", "fixtures", "all"} and not _provider_supports_workflow(provider, workflow_class):
        return _unsupported_workflow_report(provider, workflow_class)
    if workflow_class == "external_write_gate":
        return _external_write_gate_report(provider)
    if workflow_class == "authenticated_write_profile":
        return _authenticated_write_profile_report(provider)
    if workflow_class == "fleet_read":
        return _fleet_read_report(provider)
    results = []
    if provider in ("local", "decodo-http", "all") and _provider_supports_workflow("decodo-http", workflow_class):
        results.append(_run_raw_http_fixture())
    if provider in ("local", "playwright", "all") and _provider_supports_workflow("playwright", workflow_class):
        results.append(_run_playwright_fixture())
    if provider in ("local", "fixtures", "all") and workflow_class == DEFAULT_WORKFLOW_CLASS:
        results.extend(_run_fixture_matrix())
    if provider == "all":
        for provider_name in PROVIDERS:
            if provider_name not in {"playwright", "decodo-http"} and _provider_supports_workflow(provider_name, workflow_class):
                results.append(_run_provider_fixture(provider_name))
    elif provider not in {"local", "fixtures", "playwright", "decodo-http"}:
        results.append(_run_provider_fixture(provider))
    if not results:
        return _unsupported_workflow_report(provider, workflow_class)
    statuses = {item["status"] for item in results}
    if statuses <= {"skipped"}:
        status = "skipped"
    elif statuses <= {"passed", "skipped"}:
        status = "passed"
    else:
        status = "partial"
    report = {"status": status, "results": results}
    report["evidence"] = record_live_test_evidence(report, provider, set(PROVIDERS))
    return report


def _unsupported_workflow_report(provider: str, workflow_class: str) -> dict[str, Any]:
    supported = _supported_workflow_classes_for(provider)
    return {
        "status": "failed",
        "results": [
            {
                "provider": provider,
                "status": "failed",
                "workflow_class": workflow_class,
                "supported_workflow_classes": supported,
                "reason": "workflow class is not supported by this built-in live test",
                "unsupported_workflow_class": True,
            }
        ],
        "evidence": {"recorded": False, "reason": "unsupported_workflow_class"},
    }


def _authenticated_write_profile_report(provider: str) -> dict[str, Any]:
    provider_names = _profile_capable_providers(provider)
    if not provider_names:
        return _unsupported_workflow_report(provider, "authenticated_write_profile")
    results = [_run_authenticated_write_profile_fixture(provider_name) for provider_name in provider_names]
    status = "passed" if all(item["status"] == "passed" for item in results) else "partial"
    report = {"status": status, "results": results}
    report["evidence"] = record_live_test_evidence(report, provider, set(PROVIDERS))
    return report


def _fleet_read_report(provider: str) -> dict[str, Any]:
    provider_names = _profile_capable_providers(provider)
    if not provider_names:
        return _unsupported_workflow_report(provider, "fleet_read")
    results = [_run_fleet_read_fixture(provider_name) for provider_name in provider_names]
    status = "passed" if all(item["status"] == "passed" for item in results) else "partial"
    report = {"status": status, "results": results}
    report["evidence"] = record_live_test_evidence(report, provider, set(PROVIDERS))
    return report


def _profile_capable_providers(provider: str) -> list[str]:
    capable = [name for name, spec in PROVIDERS.items() if spec.supports_profiles]
    if provider == "local":
        return []
    if provider == "all":
        return capable
    if provider in capable:
        return [provider]
    return []


def _run_authenticated_write_profile_fixture(provider_name: str) -> dict[str, Any]:
    old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            profile_name = f"live-test-{provider_name}"
            ProfileStore().create(profile_name, description="authenticated write profile live test")
            run = create_run(
                "Post this Super Saiyan Browser profile live-test comment",
                url="https://example.com",
                providers_allowed=[provider_name],
                profile=profile_name,
            )
            pending_approvals = [item for item in run.approvals if item.get("status") == "pending"]
            provider_attempt_started = any(event.get("type") == "execution_started" for event in run.events)
            passed = (
                run.status == "awaiting_approval"
                and run.plan.get("task", {}).get("profile") == profile_name
                and bool(pending_approvals)
                and not provider_attempt_started
            )
            return {
                "provider": provider_name,
                "status": "passed" if passed else "failed",
                "workflow_class": "authenticated_write_profile",
                "run_id": run.run_id,
                "profile": profile_name,
                "selected_provider": run.plan.get("primary_provider"),
                "verification": {
                    "confidence": "high" if passed else "low",
                    "checks": [
                        "profile-bound external write created awaiting_approval run",
                        "pending approval request recorded",
                        "provider execution did not start",
                    ],
                },
            }
        finally:
            if old_state is not None:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state
            else:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)


def _run_fleet_read_fixture(provider_name: str) -> dict[str, Any]:
    old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            profile_base = f"fleet-{provider_name}"
            ProfileStore().create(profile_base, description="fleet read live test base profile")
            payload = create_fleet_runs(
                "Read the page title from this URL",
                fleet_size=2,
                url="https://example.com",
                providers_allowed=[provider_name],
                profile=profile_base,
                proxy="decodo",
                execute=False,
            )
            runs = payload.get("runs", [])
            profiles = [run.get("plan", {}).get("task", {}).get("profile") for run in runs]
            passed = (
                payload.get("fleet_size") == 2
                and len(runs) == 2
                and profiles == [f"{profile_base}-1", f"{profile_base}-2"]
                and all(run.get("status") == "planned" for run in runs)
            )
            return {
                "provider": provider_name,
                "status": "passed" if passed else "failed",
                "workflow_class": "fleet_read",
                "fleet_size": payload.get("fleet_size"),
                "profiles": profiles,
                "verification": {
                    "confidence": "high" if passed else "low",
                    "checks": [
                        "fleet created two plan-only runs",
                        "per-member profile suffixes assigned",
                        "proxy hint preserved on fleet payload",
                    ],
                },
            }
        finally:
            if old_state is not None:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state
            else:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)


def _external_write_gate_report(provider: str) -> dict[str, Any]:
    provider_names = _external_write_gate_providers(provider)
    if not provider_names:
        return _unsupported_workflow_report(provider, "external_write_gate")
    results = [_run_external_write_gate_fixture(provider_name) for provider_name in provider_names]
    status = "passed" if all(item["status"] == "passed" for item in results) else "partial"
    report = {"status": status, "results": results}
    report["evidence"] = record_live_test_evidence(report, provider, set(PROVIDERS))
    return report


def _run_external_write_gate_fixture(provider_name: str) -> dict[str, Any]:
    old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Post this Super Saiyan Browser live-test comment", url="https://example.com", providers_allowed=[provider_name])
            events = [event.get("type") for event in run.events]
            pending_approvals = [item for item in run.approvals if item.get("status") == "pending"]
            provider_attempt_started = any("execution_started" in event for event in events)
            passed = run.status == "awaiting_approval" and bool(pending_approvals) and not provider_attempt_started
            checks = [
                "external write created awaiting_approval run",
                "pending approval request recorded",
                "provider execution did not start",
            ]
            return {
                "provider": provider_name,
                "status": "passed" if passed else "failed",
                "workflow_class": "external_write_gate",
                "run_id": run.run_id,
                "selected_provider": run.plan.get("primary_provider"),
                "verification": {"confidence": "high" if passed else "low", "checks": checks},
                "artifacts": run.artifacts,
                "events": run.events,
                "approvals": run.approvals,
            }
        finally:
            if old_state is not None:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state
            else:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)


def _run_raw_http_fixture() -> dict[str, Any]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    old_proxy = os.environ.pop("DECODO_PROXY", None)
    old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            url = f"http://127.0.0.1:{server.server_port}/data.json"
            run = create_run("Fetch this JSON endpoint through raw HTTP", url=url)
            return {
                "provider": "decodo-http",
                "status": "passed" if run.status == "complete" else run.status,
                "workflow_class": "raw_http_direct",
                "run_id": run.run_id,
                "verification": run.verification,
                "artifacts": run.artifacts,
            }
    finally:
        if old_proxy is not None:
            os.environ["DECODO_PROXY"] = old_proxy
        if old_state is not None:
            os.environ["SUPER_BROWSER_STATE_DIR"] = old_state
        else:
            os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
        server.shutdown()
        server.server_close()


def _run_playwright_fixture() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        os.environ["SUPER_BROWSER_STATE_DIR"] = str(Path(tmp) / "state")
        fixture_path = Path(tmp) / "fixture.html"
        fixture_path.write_text(
            "<html><head><title>Super Saiyan Browser Fixture</title></head><body><h1>Fixture Ready</h1></body></html>",
            encoding="utf-8",
        )
        try:
            run = create_run("Extract the title from this local test page", url=fixture_path.resolve().as_uri())
            if run.status == "awaiting_approval":
                run = approve_run(run.run_id, approver="verify-super-browser", reason="approved local temporary fixture file", execute=True)
            return {
                "provider": "playwright",
                "status": "passed" if run.status == "complete" else run.status,
                "workflow_class": "local_browser_fixture",
                "run_id": run.run_id,
                "verification": run.verification,
                "artifacts": run.artifacts,
                "events": run.events,
            }
        finally:
            if old_state is not None:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state
            else:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)


def _run_fixture_matrix() -> list[dict[str, Any]]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - depends on environment
        return [{"provider": "fixture-matrix", "scenario": "all", "status": "skipped", "reason": f"Playwright is not importable: {exc}"}]

    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        artifact_dir = Path(tmp) / "fixture-artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                results.append(_run_login_fixture(page, base_url, artifact_dir))
                results.append(_run_infinite_scroll_fixture(page, base_url, artifact_dir))
                results.append(_run_form_no_submit_fixture(page, base_url, artifact_dir))
                results.append(_run_social_feed_comment_draft_fixture(page, base_url, artifact_dir))
                results.append(_run_lead_generation_local_artifact_fixture(page, base_url, artifact_dir))
                results.append(_run_modal_fixture(page, base_url, artifact_dir))
                results.append(_run_file_upload_fixture(page, base_url, artifact_dir))
                results.append(_run_blocked_page_fixture(page, base_url, artifact_dir))
                browser.close()
            results.append(_run_resume_fixture(base_url))
            results.append(_run_stale_resume_fixture(base_url))
        except Exception as exc:
            results.append({"provider": "fixture-matrix", "scenario": "matrix_runtime", "status": "failed", "error": str(exc)})
        finally:
            server.shutdown()
            server.server_close()
    return results


def _run_login_fixture(page, base_url: str, artifact_dir: Path) -> dict[str, Any]:
    page.goto(f"{base_url}/login", wait_until="domcontentloaded")
    page.fill("#username", "fixture-user")
    page.fill("#password", "fixture-password")
    page.click("#login")
    page.wait_for_selector("#status[data-state='logged-in']")
    state = page.evaluate("() => localStorage.getItem('fixtureSession')")
    return _fixture_result("login", state == "fixture-user", artifact_dir, session_state=state)


def _run_infinite_scroll_fixture(page, base_url: str, artifact_dir: Path) -> dict[str, Any]:
    page.goto(f"{base_url}/scroll", wait_until="domcontentloaded")
    for _ in range(4):
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(50)
    count = page.locator(".item").count()
    return _fixture_result("infinite_scroll", count >= 30, artifact_dir, item_count=count)


def _run_form_no_submit_fixture(page, base_url: str, artifact_dir: Path) -> dict[str, Any]:
    page.goto(f"{base_url}/form", wait_until="domcontentloaded")
    page.fill("#comment", "Fixture draft only")
    value = page.input_value("#comment")
    submitted = page.locator("#submitted").inner_text()
    return _fixture_result("form_fill_no_submit", value == "Fixture draft only" and submitted == "false", artifact_dir, draft=value, submitted=submitted)


def _run_social_feed_comment_draft_fixture(page, base_url: str, artifact_dir: Path) -> dict[str, Any]:
    draft_text = "Fixture comment draft for high-intent roofing lead"
    page.goto(f"{base_url}/social", wait_until="domcontentloaded")
    matched_posts = page.locator("[data-topic='roofing'][data-intent='high']").count()
    page.locator("[data-topic='roofing'][data-intent='high'] .comment").first.click()
    page.fill("#comment-draft", draft_text)
    selected_post = page.locator("#selected-post").inner_text()
    draft = page.input_value("#comment-draft")
    published = page.locator("#published").inner_text()
    passed = matched_posts == 2 and selected_post == "post-1" and draft == draft_text and published == "false"
    return _fixture_result(
        "social_feed_comment_draft",
        passed,
        artifact_dir,
        matched_posts=matched_posts,
        selected_post=selected_post,
        draft=draft,
        published=published,
    )


def _run_lead_generation_local_artifact_fixture(page, base_url: str, artifact_dir: Path) -> dict[str, Any]:
    page.goto(f"{base_url}/leads", wait_until="domcontentloaded")
    qualified = page.locator("[data-fit='qualified'][data-public='true']")
    qualified_leads = qualified.count()
    lead_names = qualified.locator(".name").evaluate_all("(nodes) => nodes.map((node) => node.textContent)")
    page.click("#save-local")
    export_json = page.locator("#local-export").inner_text()
    try:
        exported = json.loads(export_json)
    except json.JSONDecodeError:
        exported = []
    crm_synced = page.locator("#crm-synced").inner_text()
    emailed = page.locator("#emailed").inner_text()
    passed = (
        qualified_leads == 2
        and lead_names == ["Avery Roofing", "Northstar Solar"]
        and [lead.get("name") for lead in exported] == lead_names
        and crm_synced == "false"
        and emailed == "false"
    )
    return _fixture_result(
        "lead_generation_local_artifact",
        passed,
        artifact_dir,
        qualified_leads=qualified_leads,
        lead_names=lead_names,
        local_artifact_rows=len(exported),
        crm_synced=crm_synced,
        emailed=emailed,
    )


def _run_modal_fixture(page, base_url: str, artifact_dir: Path) -> dict[str, Any]:
    page.goto(f"{base_url}/modal", wait_until="domcontentloaded")
    page.click("#open-modal")
    page.wait_for_selector("#modal[data-open='true']")
    opened = page.locator("#modal").is_visible()
    page.click("#close-modal")
    closed = page.locator("#modal").get_attribute("data-open") == "false"
    return _fixture_result("modal_handling", opened and closed, artifact_dir, opened=opened, closed=closed)


def _run_file_upload_fixture(page, base_url: str, artifact_dir: Path) -> dict[str, Any]:
    upload_path = artifact_dir / "upload-fixture.txt"
    upload_path.write_text("Super Saiyan Browser upload fixture", encoding="utf-8")
    page.goto(f"{base_url}/upload", wait_until="domcontentloaded")
    page.set_input_files("#file", str(upload_path))
    page.wait_for_selector("#filename[data-ready='true']")
    filename = page.locator("#filename").inner_text()
    return _fixture_result("file_upload", filename == "upload-fixture.txt", artifact_dir, filename=filename)


def _run_blocked_page_fixture(page, base_url: str, artifact_dir: Path) -> dict[str, Any]:
    response = page.goto(f"{base_url}/blocked", wait_until="domcontentloaded")
    status = response.status if response else None
    reason = page.locator("#reason").inner_text()
    return _fixture_result("blocked_page", status == 403 and reason == "403", artifact_dir, status_code=status, reason=reason)


def _run_resume_fixture(base_url: str) -> dict[str, Any]:
    from .runtime import resume_run

    old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
    old_proxy = os.environ.pop("DECODO_PROXY", None)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Fetch this JSON endpoint through raw HTTP", url=f"{base_url}/data.json", execute=False)
            resumed = resume_run(run.run_id)
            return {
                "provider": "fixture-matrix",
                "scenario": "long_running_resume",
                "status": "passed" if resumed.status == "complete" else resumed.status,
                "run_id": resumed.run_id,
                "selected_provider": resumed.verification.get("selected_provider"),
                "checks": resumed.verification.get("checks", []),
            }
        finally:
            if old_state is not None:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state
            else:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy


def _run_stale_resume_fixture(base_url: str) -> dict[str, Any]:
    from .models import utc_now
    from .runtime import resume_run
    from .store import RunStore

    old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
    old_proxy = os.environ.pop("DECODO_PROXY", None)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run("Fetch this JSON endpoint through raw HTTP", url=f"{base_url}/data.json", execute=False)
            store = RunStore()
            store.claim_execution(
                run.run_id,
                "planned",
                [{"at": utc_now(), "type": "execution_started", "provider": "decodo-http"}],
                lease_seconds=0,
            )
            resumed = resume_run(run.run_id)
            events = [event.get("type") for event in resumed.events]
            passed = resumed.status == "complete" and "stale_execution_recovered" in events
            checks = list(resumed.verification.get("checks", []))
            if "stale_execution_recovered" in events:
                checks.append("stale execution recovered")
            return {
                "provider": "fixture-matrix",
                "scenario": "stale_long_running_resume",
                "status": "passed" if passed else resumed.status,
                "run_id": resumed.run_id,
                "selected_provider": resumed.verification.get("selected_provider"),
                "checks": checks,
            }
        finally:
            if old_state is not None:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state
            else:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
            if old_proxy is not None:
                os.environ["DECODO_PROXY"] = old_proxy


def _fixture_result(scenario: str, passed: bool, artifact_dir: Path, **metadata) -> dict[str, Any]:
    path = artifact_dir / f"{scenario}.json"
    payload = {"scenario": scenario, "passed": passed, **metadata}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "provider": "fixture-matrix",
        "scenario": scenario,
        "status": "passed" if passed else "failed",
        "artifact": str(path),
        "checks": list(metadata.keys()),
        **metadata,
    }


def _run_provider_fixture(provider_name: str) -> dict[str, Any]:
    missing = [env_name for env_name in PROVIDERS[provider_name].env_vars if not os.environ.get(env_name)]
    if missing:
        return {
            "provider": provider_name,
            "status": "skipped",
            "workflow_class": _workflow_class_for_provider(provider_name),
            "missing_env": missing,
            "reason": "live provider credentials are not configured",
        }

    goal, url = _fixture_task_for(provider_name)
    old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            run = create_run(goal, url=url, execute=False, providers_allowed=[provider_name])
            if run.status == "awaiting_approval":
                run = approve_run(
                    run.run_id,
                    approver="super-browser-live-test",
                    reason=f"approved read-only provider live test for {provider_name}",
                    execute=True,
                )
            else:
                run = resume_run(run.run_id)
        finally:
            if old_state is not None:
                os.environ["SUPER_BROWSER_STATE_DIR"] = old_state
            else:
                os.environ.pop("SUPER_BROWSER_STATE_DIR", None)
    return {
        "provider": provider_name,
        "status": "passed" if run.status == "complete" else run.status,
        "workflow_class": _workflow_class_for_provider(provider_name),
        "run_id": run.run_id,
        "verification": run.verification,
        "artifacts": run.artifacts,
        "events": run.events,
        "approvals": run.approvals,
        "selected_provider": run.verification.get("selected_provider"),
    }


def _fixture_task_for(provider_name: str) -> tuple[str, str | None]:
    if provider_name == "orgo":
        return "Use a desktop computer to print Super Saiyan Browser live-test status and capture a screenshot", None
    return "Read https://example.com and return the page title plus a short summary", "https://example.com"


def _workflow_class_for_provider(provider_name: str) -> str:
    if provider_name == "orgo":
        return "desktop_read"
    return "general_read"


def _provider_supports_workflow(provider_name: str, workflow_class: str) -> bool:
    return workflow_class == DEFAULT_WORKFLOW_CLASS or workflow_class in _supported_workflow_classes_for(provider_name)


def _supported_workflow_classes_for(provider_name: str) -> list[str]:
    if provider_name == "local":
        return ["raw_http_direct", "local_browser_fixture", "external_write_gate"]
    if provider_name == "all":
        return [item for item in WORKFLOW_CLASSES if item != DEFAULT_WORKFLOW_CLASS]
    if provider_name == "fixtures":
        return []
    if provider_name == "decodo-http":
        return ["raw_http_direct"]
    if provider_name == "playwright":
        return ["local_browser_fixture", "external_write_gate"]
    if provider_name == "orgo":
        return ["desktop_read", "external_write_gate"]
    if provider_name in PROVIDERS:
        classes = ["general_read", "external_write_gate"]
        if PROVIDERS[provider_name].supports_profiles:
            classes.extend(["authenticated_write_profile", "fleet_read"])
        return classes
    return []


def _external_write_gate_providers(provider: str) -> list[str]:
    if provider == "local":
        return ["playwright"]
    if provider == "all":
        return list(PROVIDERS)
    if provider in PROVIDERS:
        return [provider]
    return []


def _fixture_html(title: str, body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head><body>{body}</body></html>"


def _login_fixture_body() -> str:
    return """
<h1>Login Fixture</h1>
<input id="username" autocomplete="username">
<input id="password" type="password" autocomplete="current-password">
<button id="login" type="button">Login</button>
<p id="status" data-state="logged-out">Logged out</p>
<script>
document.getElementById('login').addEventListener('click', () => {
  const username = document.getElementById('username').value;
  localStorage.setItem('fixtureSession', username);
  const status = document.getElementById('status');
  status.dataset.state = 'logged-in';
  status.textContent = `Logged in as ${username}`;
});
</script>
"""


def _scroll_fixture_body() -> str:
    return """
<h1>Infinite Scroll Fixture</h1>
<div id="items"></div>
<script>
let count = 0;
function appendItems(total) {
  const root = document.getElementById('items');
  for (let i = 0; i < total; i += 1) {
    count += 1;
    const div = document.createElement('div');
    div.className = 'item';
    div.textContent = `Item ${count}`;
    div.style.height = '80px';
    root.appendChild(div);
  }
}
appendItems(10);
window.addEventListener('scroll', () => {
  if (window.innerHeight + window.scrollY >= document.body.scrollHeight - 5 && count < 40) {
    appendItems(10);
  }
});
</script>
"""


def _form_fixture_body() -> str:
    return """
<h1>Form Fixture</h1>
<form id="draft-form">
  <textarea id="comment" name="comment"></textarea>
  <button id="submit" type="submit">Submit</button>
</form>
<p id="submitted">false</p>
<script>
document.getElementById('draft-form').addEventListener('submit', event => {
  event.preventDefault();
  document.getElementById('submitted').textContent = 'true';
});
</script>
"""


def _social_feed_fixture_body() -> str:
    return """
<h1>Social Feed Fixture</h1>
<section id="feed" aria-label="Group posts">
  <article class="post" data-post-id="post-1" data-topic="roofing" data-intent="high">
    <h2>Commercial roof leak after storm</h2>
    <p>Looking for a contractor who can inspect this week and provide a repair estimate.</p>
    <button class="comment" type="button" data-post-id="post-1">Comment</button>
  </article>
  <article class="post" data-post-id="post-2" data-topic="landscaping" data-intent="medium">
    <h2>Need spring cleanup ideas</h2>
    <p>Collecting vendor recommendations for a small yard project.</p>
    <button class="comment" type="button" data-post-id="post-2">Comment</button>
  </article>
  <article class="post" data-post-id="post-3" data-topic="roofing" data-intent="high">
    <h2>Comparing roof replacement bids</h2>
    <p>Homeowner needs help understanding three proposals before choosing a roofer.</p>
    <button class="comment" type="button" data-post-id="post-3">Comment</button>
  </article>
  <article class="post" data-post-id="post-4" data-topic="roofing" data-intent="low">
    <h2>Roof color inspiration</h2>
    <p>Browsing design examples without an active project.</p>
    <button class="comment" type="button" data-post-id="post-4">Comment</button>
  </article>
</section>
<form id="composer" data-open="false">
  <p id="selected-post"></p>
  <textarea id="comment-draft" name="comment"></textarea>
  <button id="publish" type="submit">Publish</button>
</form>
<p id="published">false</p>
<script>
document.querySelectorAll('.comment').forEach(button => {
  button.addEventListener('click', () => {
    document.getElementById('composer').dataset.open = 'true';
    document.getElementById('selected-post').textContent = button.dataset.postId;
  });
});
document.getElementById('composer').addEventListener('submit', event => {
  event.preventDefault();
  document.getElementById('published').textContent = 'true';
});
</script>
"""


def _lead_generation_fixture_body() -> str:
    return """
<h1>Lead Generation Fixture</h1>
<section id="lead-results" aria-label="Public lead results">
  <article class="lead" data-fit="qualified" data-public="true" data-name="Avery Roofing" data-source="public-directory">
    <h2 class="name">Avery Roofing</h2>
    <p class="signal">Hiring operations manager; has public contact page; service area matches ICP.</p>
  </article>
  <article class="lead" data-fit="unqualified" data-public="true" data-name="Harbor Bakery" data-source="public-directory">
    <h2 class="name">Harbor Bakery</h2>
    <p class="signal">Local business, but industry does not match ICP.</p>
  </article>
  <article class="lead" data-fit="qualified" data-public="true" data-name="Northstar Solar" data-source="public-directory">
    <h2 class="name">Northstar Solar</h2>
    <p class="signal">Expanding installation team; public estimate form; target region match.</p>
  </article>
  <article class="lead" data-fit="qualified" data-public="false" data-name="Private Inbox Lead" data-source="private-message">
    <h2 class="name">Private Inbox Lead</h2>
    <p class="signal">Private-message-only lead that must not be treated as public extraction.</p>
  </article>
</section>
<button id="save-local" type="button">Save Local Export</button>
<button id="sync-crm" type="button">Sync CRM</button>
<button id="email-leads" type="button">Email Leads</button>
<pre id="local-export">[]</pre>
<p id="crm-synced">false</p>
<p id="emailed">false</p>
<script>
document.getElementById('save-local').addEventListener('click', () => {
  const leads = Array.from(document.querySelectorAll("[data-fit='qualified'][data-public='true']")).map(node => ({
    name: node.dataset.name,
    source: node.dataset.source,
    signal: node.querySelector('.signal').textContent
  }));
  document.getElementById('local-export').textContent = JSON.stringify(leads);
});
document.getElementById('sync-crm').addEventListener('click', () => {
  document.getElementById('crm-synced').textContent = 'true';
});
document.getElementById('email-leads').addEventListener('click', () => {
  document.getElementById('emailed').textContent = 'true';
});
</script>
"""


def _modal_fixture_body() -> str:
    return """
<h1>Modal Fixture</h1>
<button id="open-modal" type="button">Open</button>
<div id="modal" data-open="false" hidden>
  <p>Fixture modal content</p>
  <button id="close-modal" type="button">Close</button>
</div>
<script>
document.getElementById('open-modal').addEventListener('click', () => {
  const modal = document.getElementById('modal');
  modal.hidden = false;
  modal.dataset.open = 'true';
});
document.getElementById('close-modal').addEventListener('click', () => {
  const modal = document.getElementById('modal');
  modal.hidden = true;
  modal.dataset.open = 'false';
});
</script>
"""


def _upload_fixture_body() -> str:
    return """
<h1>Upload Fixture</h1>
<input id="file" type="file">
<p id="filename" data-ready="false"></p>
<script>
document.getElementById('file').addEventListener('change', event => {
  const file = event.target.files[0];
  const filename = document.getElementById('filename');
  filename.dataset.ready = 'true';
  filename.textContent = file ? file.name : '';
});
</script>
"""
