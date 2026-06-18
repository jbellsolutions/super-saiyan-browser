import re
import unittest
from pathlib import Path

from super_browser.providers import PROVIDERS


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DOCS = ROOT / "references" / "providers"
ROUTING_PLAYBOOK = (ROOT / "references" / "routing-playbook.md").read_text()
PROVIDER_MATRIX = (ROOT / "references" / "provider-matrix.md").read_text()


class ProviderDocsParityTests(unittest.TestCase):
    def test_every_provider_has_ssot_doc(self):
        for name in PROVIDERS:
            path = PROVIDER_DOCS / f"{name}.md"
            self.assertTrue(path.exists(), f"missing SSOT doc for {name}")

    def test_anti_bot_flags_match_playbook(self):
        playbook_row = re.search(
            r"Anti-bot hardened browsing \| `[^`]+` \| `([^`]+)`",
            ROUTING_PLAYBOOK,
        )
        self.assertIsNotNone(playbook_row)
        listed = {
            item.strip().split("(")[0].strip()
            for item in playbook_row.group(1).split(",")
        }
        for name in listed:
            if name == "browserbase":
                self.assertEqual(PROVIDERS[name].stability, "docs-only")
                continue
            self.assertTrue(PROVIDERS[name].supports_anti_bot, name)

    def test_docs_only_providers_excluded_from_routing_candidates(self):
        from super_browser.router import _candidate_providers
        from super_browser.models import TaskSpec

        task = TaskSpec(goal="Extract https://example.com")
        candidates = _candidate_providers(task)
        self.assertNotIn("browserbase", candidates)

    def test_browserbase_in_provider_matrix(self):
        self.assertIn("Browserbase", PROVIDER_MATRIX)
        self.assertIn("docs-only", PROVIDER_MATRIX)

    def test_combo_playbook_exists(self):
        self.assertTrue((ROOT / "references" / "combo-playbook.md").exists())


if __name__ == "__main__":
    unittest.main()
