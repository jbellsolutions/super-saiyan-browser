import os
import tempfile
import unittest

from super_browser.fleet import create_fleet_runs
from super_browser.models import Plan, TaskSpec
from super_browser.profiles import ProfileStore
from super_browser.proxy import build_decodo_proxy_url, resolve_proxy_url, sticky_port_for_key
from super_browser.router import build_plan, infer_task, provider_sequence_constraint_failures


class ProfileStoreTests(unittest.TestCase):
    def test_create_list_get_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            store = ProfileStore()
            created = store.create("ig-account-1", description="test profile")
            self.assertEqual(created.name, "ig-account-1")
            self.assertEqual(len(store.list()), 1)
            self.assertEqual(store.get("ig-account-1").description, "test profile")
            store.bind_provider_id("ig-account-1", "steel", "sess-abc")
            self.assertEqual(store.resolve_provider_id("ig-account-1", "steel"), "sess-abc")
            self.assertTrue(store.delete("ig-account-1"))
            self.assertIsNone(store.get("ig-account-1"))

    def test_duplicate_profile_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            store = ProfileStore()
            store.create("dup")
            with self.assertRaisesRegex(ValueError, "Profile already exists"):
                store.create("dup")


class ProfileRoutingTests(unittest.TestCase):
    def setUp(self):
        self._old_state = os.environ.get("SUPER_BROWSER_STATE_DIR")
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["SUPER_BROWSER_STATE_DIR"] = self._tmpdir.name
        ProfileStore().create("acct-a")

    def tearDown(self):
        self._tmpdir.cleanup()
        if self._old_state is not None:
            os.environ["SUPER_BROWSER_STATE_DIR"] = self._old_state
        else:
            os.environ.pop("SUPER_BROWSER_STATE_DIR", None)

    def test_profile_task_requires_existing_profile(self):
        with self.assertRaisesRegex(ValueError, "Profile not found"):
            infer_task("Read dashboard", profile="missing-profile")

    def test_profile_routes_to_profile_capable_provider(self):
        plan = build_plan(
            infer_task(
                "Read dashboard alerts",
                url="https://example.com",
                profile="acct-a",
                providers_allowed=["steel", "playwright"],
            )
        )
        self.assertEqual(plan.primary_provider, "steel")
        self.assertEqual(plan.task.profile, "acct-a")

    def test_missing_profile_in_plan_sequence_fails(self):
        task = TaskSpec(
            goal="Read dashboard",
            url="https://example.com",
            profile="ghost",
            providers_allowed=["steel"],
        )
        plan = Plan(task=task, primary_provider="steel", fallback_providers=[])
        failures = provider_sequence_constraint_failures(plan)
        self.assertTrue(any(item["type"] == "provider_profile_missing" for item in failures))

    def test_proxy_on_non_injectable_provider_fails(self):
        task = TaskSpec(
            goal="Use a desktop computer to open a spreadsheet",
            url="https://example.com",
            proxy="decodo",
            needs_desktop=True,
            providers_allowed=["orgo"],
        )
        plan = Plan(task=task, primary_provider="orgo", fallback_providers=[])
        failures = provider_sequence_constraint_failures(plan)
        self.assertTrue(any(item["type"] == "provider_proxy_constraint_violation" for item in failures))


class ProxyResolutionTests(unittest.TestCase):
    def test_sticky_port_is_deterministic(self):
        self.assertEqual(sticky_port_for_key("acct-a"), sticky_port_for_key("acct-a"))

    def test_build_decodo_proxy_url_uses_env(self):
        old = os.environ.get("DECODO_PROXY")
        try:
            os.environ["DECODO_PROXY"] = "http://user:pass@gate.decodo.com:10001"
            url = build_decodo_proxy_url(profile_name="acct-a")
            self.assertIn("gate.decodo.com", url or "")
            self.assertIn("user:pass", url or "")
        finally:
            if old is None:
                os.environ.pop("DECODO_PROXY", None)
            else:
                os.environ["DECODO_PROXY"] = old

    def test_resolve_proxy_url_honors_decodo_hint(self):
        old = os.environ.get("DECODO_PROXY")
        try:
            os.environ["DECODO_PROXY"] = "http://user:pass@gate.decodo.com:10001"
            task = TaskSpec(goal="Read page", url="https://example.com", proxy="decodo", profile="acct-a")
            resolved = resolve_proxy_url(task)
            self.assertIsNotNone(resolved)
            self.assertIn("gate.decodo.com", resolved or "")
        finally:
            if old is None:
                os.environ.pop("DECODO_PROXY", None)
            else:
                os.environ["DECODO_PROXY"] = old


class FleetRunTests(unittest.TestCase):
    def test_create_fleet_runs_assigns_member_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SUPER_BROWSER_STATE_DIR"] = tmp
            ProfileStore().create("fleet-base")
            payload = create_fleet_runs(
                "Read titles",
                fleet_size=2,
                url="https://example.com",
                profile="fleet-base",
                proxy="decodo",
                execute=False,
                providers_allowed=["steel"],
            )
            profiles = [run["plan"]["task"]["profile"] for run in payload["runs"]]
            self.assertEqual(profiles, ["fleet-base-1", "fleet-base-2"])
            self.assertEqual(payload["fleet_size"], 2)


if __name__ == "__main__":
    unittest.main()
