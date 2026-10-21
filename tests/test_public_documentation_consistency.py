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
        self.assertIn("Verification snapshot", readme)
        self.assertRegex(readme, r"(?:captured )?at main SHA `[0-9a-f]{40}`")
        self.assertRegex(readme, r"\d+ check runs succeeded, \d+ remained in progress, and \d+ were skipped")
        self.assertIn("skipped checks are not passes", readme.lower())
        self.assertIn("github.com/sourabhJainR/AUREN/actions", readme)
        self.assertIn("does not claim that its SHA is the current main head", readme)

        self.assertIn("Verification snapshot", site)
        self.assertRegex(site, r"at main SHA <code>[0-9a-f]{40}</code>")
        self.assertRegex(site, r"\d+ check runs succeeded, \d+ remained in progress, and \d+ were skipped")
        self.assertIn("skipped checks are not passes", site.lower())
        self.assertIn("github.com/sourabhJainR/AUREN/actions", site)
        self.assertIn("does not claim that its SHA is the current main head", site)

    def test_readme_status_and_section_format_are_current(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        import re
        self.assertRegex(readme, r"## Project status \(reviewed \d{4}-\d{2}-\d{2}\)")
        self.assertNotIn(r"\n\n## Deterministic repository intelligence", readme)

    def test_standalone_readiness_matrix_tracks_external_gates(self):
        matrix = (ROOT / "docs" / "STANDALONE_READINESS_AND_GAP_MATRIX.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        site = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn("STANDALONE_READINESS_AND_GAP_MATRIX.md", readme)
        self.assertIn("STANDALONE_READINESS_AND_GAP_MATRIX.md", site)
        self.assertIn("Verify hard termination", matrix)
        self.assertIn("Prove cancellation and absolute deadlines", matrix)
        self.assertIn("independent evaluator", matrix.lower())
        self.assertIn("sealed corpus custody", matrix.lower())
        self.assertIn("skipped jobs are not passes", matrix.lower())
        self.assertIn("clean-install smoke test", matrix)
        provider_tests = (ROOT / "tests" / "test_provider_backed_crew_adapters.py").read_text(encoding="utf-8")
        runtime_bootstrap = (ROOT / ".ai-harness" / "runtime" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("test_supervised_registration_uses_controller_owned_progress_journal", provider_tests)
        self.assertIn("register_supervised_provider_backed_crew_adapters", runtime_bootstrap)


if __name__ == "__main__":
    unittest.main()
