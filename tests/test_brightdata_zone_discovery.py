import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from super_browser.brightdata.zone_discovery import (
    DiscoveredZones,
    discover_and_apply,
    discover_zones,
    discovery_report,
    resolve_api_key,
    write_discovered_env,
)
from super_browser.brightdata.zones import brightdata_config, missing_env_for_lane
from super_browser.env_file import load_env_file, merge_env_file


class BrightDataZoneDiscoveryTests(unittest.TestCase):
    def test_resolve_api_key_from_mcp_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp_path = Path(tmp) / "mcp.json"
            mcp_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "brightdata": {"url": "https://mcp.brightdata.com/mcp?token=test-token-123"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                with patch("super_browser.brightdata.zone_discovery._mcp_config_paths", return_value=[mcp_path]):
                    key, source = resolve_api_key()
            self.assertEqual(key, "test-token-123")
            self.assertEqual(source, f"mcp:{mcp_path}")

    @patch("super_browser.brightdata.zone_discovery.BrightDataClient")
    def test_discover_zones_maps_types_and_serp_fallback(self, client_mock):
        instance = client_mock.return_value
        instance.request_json.side_effect = [
            [
                {"name": "mcp_unlocker", "type": "unblocker"},
                {"name": "mcp_browser", "type": "browser_api"},
            ],
            {"password": ["browser-pass"]},
        ]
        with patch.dict(os.environ, {"BRIGHTDATA_API_KEY": "key"}, clear=True):
            discovered = discover_zones()
        self.assertEqual(discovered.unlocker_zone, "mcp_unlocker")
        self.assertEqual(discovered.serp_zone, "mcp_unlocker")
        self.assertTrue(discovered.serp_uses_unlocker_fallback)
        self.assertEqual(discovered.browser_zone, "mcp_browser")
        self.assertEqual(discovered.browser_password, "browser-pass")

    @patch("super_browser.brightdata.zone_discovery.discover_zones")
    def test_discover_and_apply_sets_env(self, discover_mock):
        discover_mock.return_value = DiscoveredZones(
            "mcp:token",
            "mcp_unlocker",
            "mcp_unlocker",
            "mcp_browser",
            "browser-pass",
            True,
            (),
        )
        with patch.dict(os.environ, {}, clear=True):
            with patch("super_browser.brightdata.zone_discovery.resolve_api_key", return_value=("abc", "mcp")):
                discover_and_apply()
                self.assertEqual(os.environ["BRIGHTDATA_API_KEY"], "abc")
                self.assertEqual(os.environ["BRIGHTDATA_UNLOCKER_ZONE"], "mcp_unlocker")
                self.assertEqual(os.environ["BRIGHTDATA_BROWSER_ZONE"], "mcp_browser")
                self.assertEqual(os.environ["BRIGHTDATA_BROWSER_PASSWORD"], "browser-pass")
                self.assertIsNone(os.environ.get("BRIGHTDATA_SERP_ZONE"))

    def test_missing_env_for_serp_accepts_unlocker_fallback(self):
        import super_browser.brightdata.zones as zones_module

        zones_module._discovery_attempted = True
        with patch.dict(
            os.environ,
            {"BRIGHTDATA_API_KEY": "k", "BRIGHTDATA_UNLOCKER_ZONE": "mcp_unlocker"},
            clear=True,
        ):
            self.assertEqual(missing_env_for_lane("brightdata-serp"), [])
            cfg = brightdata_config()
            self.assertEqual(cfg.serp_zone, "mcp_unlocker")
            self.assertTrue(cfg.serp_uses_unlocker_fallback)

    def test_write_discovered_env_merges_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("EXISTING=value\nBRIGHTDATA_UNLOCKER_ZONE=keep-me\n", encoding="utf-8")
            with patch(
                "super_browser.brightdata.zone_discovery.discover_and_apply",
                return_value=DiscoveredZones("env", "new_unlocker", None, None, None, False, ()),
            ):
                with patch(
                    "super_browser.brightdata.zone_discovery.resolve_api_key",
                    return_value=("secret-key", "env"),
                ):
                    payload = write_discovered_env(env_path)
            text = env_path.read_text(encoding="utf-8")
            self.assertIn("EXISTING=value", text)
            self.assertIn("BRIGHTDATA_UNLOCKER_ZONE=keep-me", text)
            self.assertIn("BRIGHTDATA_API_KEY=secret-key", text)
            self.assertIn("BRIGHTDATA_API_KEY", payload["written_vars"])
            self.assertNotIn("BRIGHTDATA_UNLOCKER_ZONE", payload["written_vars"])

    def test_discovery_report_is_redacted(self):
        with patch(
            "super_browser.brightdata.zone_discovery.discover_zones",
            return_value=DiscoveredZones("mcp", "mcp_unlocker", "mcp_unlocker", None, None, True, ("note",)),
        ):
            with patch("super_browser.brightdata.zone_discovery.resolve_api_key", return_value=("secret", "mcp")):
                report = discovery_report()
        self.assertTrue(report["api_key_configured"])
        self.assertEqual(report["unlocker_zone"], "mcp_unlocker")
        self.assertNotIn("secret", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
