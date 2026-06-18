import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from super_browser.adapters import (
    BrightDataDatasetAdapter,
    BrightDataSerpAdapter,
    BrightDataUnlockerAdapter,
    get_adapter,
)
from super_browser.models import Plan, TaskSpec
from super_browser.router import build_plan, infer_task, rank_providers
from super_browser.brightdata.datasets import dataset_tool_for_url


class BrightDataAdapterTests(unittest.TestCase):
    def test_get_adapter_registers_brightdata_providers(self):
        self.assertEqual(get_adapter("brightdata-unlocker").name, "brightdata-unlocker")
        self.assertEqual(get_adapter("brightdata-serp").name, "brightdata-serp")
        self.assertEqual(get_adapter("brightdata-dataset").name, "brightdata-dataset")
        self.assertEqual(get_adapter("brightdata-browser").name, "brightdata-browser")

    def test_infer_task_routes_serp_goal(self):
        task = infer_task("Google search: commercial cleaning companies Texas")
        self.assertEqual(task.serp_query, "commercial cleaning companies Texas")
        ranked = rank_providers(task)
        self.assertEqual(ranked[0], "brightdata-serp")

    def test_dataset_tool_matches_linkedin_company(self):
        match = dataset_tool_for_url("https://www.linkedin.com/company/microsoft")
        self.assertIsNotNone(match)
        self.assertEqual(match.tool, "linkedin_company_profile")

    def test_unlocker_adapter_missing_env_is_blocked(self):
        adapter = BrightDataUnlockerAdapter()
        plan = Plan(task=TaskSpec(goal="Read page", url="https://example.com"), primary_provider="brightdata-unlocker")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=True):
                result = adapter.execute(plan, "run_test", Path(tmp))
        self.assertEqual(result.status, "blocked")
        self.assertIn("BRIGHTDATA", result.error or "")

    @patch("super_browser.adapters.unlock_url")
    def test_unlocker_adapter_saves_markdown(self, unlock_mock):
        unlock_mock.return_value = {"content": "# Hello", "content_length": 7, "url": "https://example.com", "zone": "z"}
        adapter = BrightDataUnlockerAdapter()
        plan = Plan(task=TaskSpec(goal="Read page", url="https://example.com"), primary_provider="brightdata-unlocker")
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp)
            with patch.dict(
                os.environ,
                {"BRIGHTDATA_API_KEY": "test-key", "BRIGHTDATA_UNLOCKER_ZONE": "unlocker1"},
                clear=True,
            ):
                result = adapter.execute(plan, "run_test", artifact_dir)
            self.assertEqual(result.status, "complete", result.error)
            output = artifact_dir / "brightdata-unlocker-output.json"
            self.assertTrue(output.exists())
            payload = json.loads(output.read_text())
            self.assertEqual(payload["provider"], "brightdata-unlocker")

    @patch("super_browser.adapters.brightdata_search")
    def test_serp_adapter_complete(self, search_mock):
        search_mock.return_value = {"query": "pizza", "results": "result markdown"}
        adapter = BrightDataSerpAdapter()
        task = infer_task("Google search: pizza")
        plan = Plan(task=task, primary_provider="brightdata-serp")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {"BRIGHTDATA_API_KEY": "test-key", "BRIGHTDATA_SERP_ZONE": "serp1"},
                clear=True,
            ):
                result = adapter.execute(plan, "run_test", Path(tmp))
        self.assertEqual(result.status, "complete")

    @patch("super_browser.adapters.scrape_dataset_url")
    def test_dataset_adapter_complete(self, scrape_mock):
        scrape_mock.return_value = {"tool": "linkedin_company_profile", "data": {"name": "Acme"}}
        adapter = BrightDataDatasetAdapter()
        task = infer_task("Extract company profile", url="https://www.linkedin.com/company/acme")
        plan = Plan(task=task, primary_provider="brightdata-dataset")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"BRIGHTDATA_API_KEY": "test-key"}, clear=True):
                result = adapter.execute(plan, "run_test", Path(tmp))
        self.assertEqual(result.status, "complete")

    def test_build_plan_includes_brightdata_unlocker_for_meta_url(self):
        task = infer_task("Read public Meta ad library page", url="https://www.facebook.com/ads/library/")
        plan = build_plan(task, deliberation_rounds=3)
        self.assertIn(plan.primary_provider, {"brightdata-unlocker", "brightdata-dataset", "browser-use", "hyperbrowser"})


if __name__ == "__main__":
    unittest.main()
