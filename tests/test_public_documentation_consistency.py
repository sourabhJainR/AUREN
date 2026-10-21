"""Keep the public AUREN documentation aligned with the canonical principle registry."""
import unittest
from pathlib import Path

from portable.ai_era_principles import PRINCIPLES

ROOT = Path(__file__).resolve().parents[1]


class TestPublicDocumentationConsistency(unittest.TestCase):
    def test_pages_entrypoints_are_identical(self):
        for name in ("index.html", "404.html"):
            with self.subTest(entrypoint=name):
                self.assertEqual(
                    (ROOT / "site" / name).read_text(encoding="utf-8"),
                    (ROOT / "docs" / name).read_text(encoding="utf-8"),
                )

    def test_pages_mirror_contract_runs_on_pull_requests(self):
        workflow = (
            ROOT / ".github" / "workflows" / "pages-mirror-contract.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertIn("cmp -s site/index.html docs/index.html", workflow)
        self.assertIn("cmp -s site/404.html docs/404.html", workflow)

    def test_only_canonical_pages_workflow_can_deploy(self):
        canonical = (ROOT / ".github" / "workflows" / "deploy-pages.yml").read_text(encoding="utf-8")
        legacy = (ROOT / ".github" / "workflows" / "jekyll-gh-pages.yml").read_text(encoding="utf-8")
        self.assertIn("'.github/workflows/jekyll-gh-pages.yml'", canonical)
        self.assertIn("site/", canonical)
        self.assertIn("workflow_dispatch:", legacy)
        self.assertNotIn("  push:", legacy)
        self.assertNotIn("actions/deploy-pages@", legacy)
        self.assertNotIn("actions/upload-pages-artifact@", legacy)

    def test_every_canonical_principle_is_in_public_docs(self):
        paths = [
            ROOT / "README.md",
            ROOT / "site" / "index.html",
            ROOT / "docs" / "AI_ERA_FOUNDATION_PRINCIPLES.md",
        ]
        for path in paths:
            content = path.read_text(encoding="utf-8")
            for principle in PRINCIPLES:
                with self.subTest(path=str(path.relative_to(ROOT)), principle=principle["id"]):
                    self.assertIn(principle["id"], content)

    def test_public_status_does_not_list_closed_issues_as_open_trackers(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        site = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        unified = (ROOT / "docs" / "UNIFIED_GRAPH_CREW_RUNTIME.md").read_text(encoding="utf-8")
        self.assertNotIn("remain tracked in [issue #438]", readme)
        self.assertNotIn("Track issue #379", site)
        self.assertNotIn("Track issue #471", site)
        self.assertIn("issue #534", site)
        self.assertIn("issue [#438]", unified)
        self.assertIn("was closed", unified)

    def test_release_version_is_synchronized(self):
        import json
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        plugin = json.loads(
            (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        site = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(version, plugin["version"])
        self.assertIn(f"Current release:** `{version}`", readme)
        self.assertIn(version, site)
        self.assertIn("docs/RESPONSIVE_EXECUTION_AND_TIMEOUTS.md", readme)

    def test_readme_status_and_section_format_are_current(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        import re
        self.assertRegex(readme, r"## Project status \\(reviewed \\d{4}-\\d{2}-\\d{2}\\)")
        self.assertNotIn(r"\n\n## Deterministic repository intelligence", readme)


if __name__ == "__main__":
    unittest.main()
