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

    def test_public_status_is_a_dated_snapshot_not_a_stale_head_claim(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        site = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        for content in (readme, site):
            self.assertIn("Verification snapshot", content)
            self.assertIn("57b1a2b367f32b3ab47a1853664a5c47f227aa6e", content)
            self.assertIn("22 check runs succeeded and 4", content)
            self.assertIn("skipped checks are not passes", content.lower())
            self.assertIn("github.com/sourabhJainR/AUREN/actions", content)
            self.assertIn("does not claim that its SHA is the current main head", content)

    def test_readme_status_and_section_format_are_current(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        import re
        self.assertRegex(readme, r"## Project status \(reviewed \d{4}-\d{2}-\d{2}\)")
        self.assertNotIn(r"\n\n## Deterministic repository intelligence", readme)


    def test_standalone_readiness_matrix_and_provider_progress_contract(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        site = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        matrix = (ROOT / "docs" / "STANDALONE_READINESS_AND_GAP_MATRIX.md").read_text(encoding="utf-8")
        provider_tests = (ROOT / "tests" / "test_provider_backed_crew_adapters.py").read_text(encoding="utf-8")
        graph_tests = (ROOT / "tests" / "test_graph_agent_team_crew_dispatch.py").read_text(encoding="utf-8")
        self.assertIn("STANDALONE_READINESS_AND_GAP_MATRIX.md", readme)
        self.assertIn("STANDALONE_READINESS_AND_GAP_MATRIX.md", site)
        self.assertIn("Prove hard termination", matrix)
        self.assertIn("independent evaluation", matrix)
        self.assertIn("skipped checks are not passes", matrix.lower())
        self.assertIn("test_hung_provider_is_bounded_even_if_invoker_ignores_timeout", provider_tests)
        self.assertIn("test_provider_timeout_is_durable_blocked_progress", provider_tests)
        self.assertIn("test_provider_progress_uses_supervisor_identity_through_graph_dispatch", graph_tests)

if __name__ == "__main__":
    unittest.main()
