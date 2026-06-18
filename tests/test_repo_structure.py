import ast
import os
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepoStructureTests(unittest.TestCase):
    def test_plugin_manifest_exists(self):
        self.assertTrue((ROOT / ".codex-plugin" / "plugin.json").exists())
        self.assertTrue((ROOT / ".mcp.json").exists())

    def test_required_skills_exist(self):
        required = [
            "super-browser-orchestrator",
            "super-browser-planner",
            "super-browser-verifier",
            "browser-use-specialist",
            "playwright-specialist",
            "orgo-specialist",
            "airtop-specialist",
            "decodo-http-specialist",
            "hyperbrowser-specialist",
            "steel-specialist",
            "publishing-safety-specialist",
        ]
        for name in required:
            self.assertTrue((ROOT / "skills" / name / "SKILL.md").exists(), name)

    def test_python_package_includes_plugin_asset_tree(self):
        pyproject = (ROOT / "pyproject.toml").read_text()
        required_paths = [
            '"share/super-browser"',
            '"share/super-browser/.codex-plugin"',
            '"share/super-browser/examples/workflows"',
            '"share/super-browser/mcp"',
            '"share/super-browser/references"',
            '"share/super-browser/scripts"',
            '"share/super-browser/src/super_browser"',
            '"share/super-browser/tests"',
            ".codex-plugin/plugin.json",
            ".mcp.json",
            "README.md",
            "SKILL.md",
            "mcp/super-browser-server",
            "scripts/super-browser",
            "scripts/verify-super-browser",
            "references/provider-matrix.md",
            "examples/workflows/extraction.json",
            "src/super_browser/cli.py",
            "src/super_browser/bundle.py",
            "src/super_browser/env_checklist.py",
            "src/super_browser/production.py",
            "tests/test_store_cli_mcp.py",
        ]
        for path in required_paths:
            self.assertIn(path, pyproject, path)
        for skill_path in (ROOT / "skills").glob("*/SKILL.md"):
            self.assertIn(str(skill_path.relative_to(ROOT)), pyproject, skill_path.parent.name)

    def test_examples_parse(self):
        for path in (ROOT / "examples").glob("*.py"):
            ast.parse(path.read_text(), filename=str(path))

    def test_workflow_examples_exist(self):
        examples = list((ROOT / "examples" / "workflows").glob("*.json"))
        self.assertGreaterEqual(len(examples), 7)

    def test_verification_entrypoint_exists(self):
        script = ROOT / "scripts" / "verify-super-browser"
        self.assertTrue(script.exists())
        self.assertTrue(os.access(script, os.X_OK))
        text = script.read_text()
        self.assertIn("python3 -m unittest discover -s tests", text)
        self.assertIn("live-test --provider fixtures", text)
        self.assertIn("live-test --provider all", text)
        self.assertIn("production-readiness", text)
        self.assertIn("bundle-manifest", text)
        self.assertIn("env-checklist", text)
        self.assertIn("VALIDATOR_PYTHON", text)
        self.assertIn("SUPER_BROWSER_STATE_DIR", text)
        self.assertIn("PYTHONPYCACHEPREFIX", text)
        self.assertIn("mktemp -d", text)
        self.assertIn("remove_repo_pycache", text)
        self.assertIn("trap cleanup EXIT", text)

    def test_github_actions_workflow_exists(self):
        workflow = ROOT / ".github" / "workflows" / "verify.yml"
        self.assertTrue(workflow.exists())
        text = workflow.read_text()
        self.assertIn("Verify Super Saiyan Browser", text)
        self.assertIn("./scripts/verify-super-browser", text)
        self.assertIn("playwright install chromium", text)


if __name__ == "__main__":
    unittest.main()
