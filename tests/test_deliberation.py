import unittest

from super_browser.deliberation import DEFAULT_LOOPS_COUNCIL, DEFAULT_LOOPS_DIRECT, deliberate, deliberate_weekly_intelligence
from super_browser.models import TaskSpec
from super_browser.providers import PROVIDERS
from super_browser.router import build_plan, infer_task


class DeliberationTests(unittest.TestCase):
    def test_simple_extraction_completes_three_loops(self):
        plan = build_plan(infer_task("Extract product names from https://example.com/products"))
        self.assertEqual(plan.mode, "direct")
        self.assertTrue(plan.council_report["deliberation_complete"])
        self.assertEqual(plan.council_report["deliberation_loop_count"], DEFAULT_LOOPS_DIRECT)
        self.assertEqual(plan.council_report["execution_pattern"], "single")

    def test_anti_bot_council_uses_five_loops(self):
        plan = build_plan(infer_task("Search Facebook Ads Library behind Cloudflare and extract advertisers"))
        self.assertEqual(plan.mode, "council")
        self.assertTrue(plan.council_report["deliberation_complete"])
        self.assertGreaterEqual(plan.council_report["deliberation_loop_count"], DEFAULT_LOOPS_COUNCIL)

    def test_browserbase_never_becomes_primary_without_adapter(self):
        plan = build_plan(infer_task("Use Browserbase Stagehand to scrape https://example.com"))
        self.assertNotEqual(plan.primary_provider, "browserbase")
        recs = plan.council_report.get("documented_recommendations") or []
        if recs:
            self.assertEqual(recs[0]["provider"], "browserbase")
            self.assertEqual(recs[0]["status"], "docs-only")

    def test_deliberation_rounds_override(self):
        plan = build_plan(
            infer_task("Extract titles from https://example.com"),
            deliberation_rounds=5,
        )
        self.assertEqual(plan.council_report["deliberation_loop_count"], 5)

    def test_weekly_intelligence_no_op_when_unchanged(self):
        verdict = deliberate_weekly_intelligence("hyperbrowser", current_summary="same", fetched_summary="same")
        self.assertFalse(verdict["apply"])
        self.assertEqual(verdict["verdict"], "no_op")

    def test_weekly_intelligence_apply_when_changed(self):
        verdict = deliberate_weekly_intelligence("steel", current_summary="old", fetched_summary="new capability")
        self.assertTrue(verdict["apply"])
        self.assertEqual(verdict["verdict"], "apply")

    def test_deliberate_missing_providers(self):
        result = deliberate(
            TaskSpec(goal="test"),
            [],
            mode="direct",
            missing_env=lambda _vars: [],
        )
        self.assertFalse(result.deliberation_complete)


if __name__ == "__main__":
    unittest.main()
