from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATHS = (
    ROOT / "skills" / "ai-coding-orchestrator" / "SKILL.md",
    ROOT / ".agents" / "skills" / "ai-coding-orchestrator" / "SKILL.md",
    ROOT / ".claude" / "skills" / "ai-coding-orchestrator" / "SKILL.md",
)
RUNTIME_PATHS = (
    ROOT / ".ai-harness" / "runtime" / "tool_runner.py",
    ROOT / ".ai-harness" / "runtime" / "lsp_server.py",
    ROOT / ".ai-harness" / "runtime" / "feedback_loop.py",
    ROOT / ".ai-harness" / "runtime" / "auto_compaction.py",
)
COMPOSED_SKILLS = (
    ROOT / "skills" / "engineering" / "retro" / "SKILL.md",
    ROOT / "skills" / "engineering" / "research" / "SKILL.md",
    ROOT / "skills" / "engineering" / "prototype" / "SKILL.md",
    ROOT / "skills" / "engineering" / "resolving-merge-conflicts" / "SKILL.md",
    ROOT / "skills" / "engineering" / "interactive-documentation" / "SKILL.md",
)


class ContractAlignmentTests(unittest.TestCase):
    def test_all_skill_entrypoints_are_identical(self) -> None:
        contents = [path.read_text(encoding="utf-8") for path in SKILL_PATHS]
        self.assertTrue(all(contents), "canonical skill entrypoints must exist")
        self.assertEqual(len(set(contents)), 1, "skill entrypoints have diverged")
        canonical = contents[0]
        for token in (
            "portable.task_planner.TaskPlan",
            "portable.impact_analysis",
            "portable.repo_intelligence.RepositoryMap",
            "CodebaseIndex",
            "ContextEvidence",
            "skills are orchestration surfaces",
            ".ai-harness/runtime/tool_runner.py",
            ".ai-harness/runtime/lsp_server.py",
            ".ai-harness/runtime/feedback_loop.py",
            ".ai-harness/runtime/auto_compaction.py",
            "interactive-documentation",
            "downgrade=explicit_install_only",
        ):
            self.assertIn(token, canonical)

    def test_grilling_and_rework_are_core_contracts(self) -> None:
        policy = ROOT / "skills" / "engineering" / "grilling-and-rework.md"
        self.assertTrue(policy.is_file(), "core grilling/rework policy must be present")
        text = policy.read_text(encoding="utf-8")
        for token in (
            "dependency-aware rounds",
            "user retains authority",
            "rework the implementation, not the verdict",
            "at most two attempts",
            "exact current head",
            "Unresolved decisions block only tasks that depend on them",
        ):
            self.assertIn(token, text)
        canonical = (ROOT / "skills" / "ai-coding-orchestrator" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("skills/engineering/grilling-and-rework.md", canonical)
        self.assertIn("Core grilling and rework foundation", canonical)
        self.assertIn("Actionable output contract", text)
        self.assertIn("expected result or acceptance condition", text)
        self.assertIn("Make blockers actionable", text)
        self.assertIn("Actionable output is part of completion", canonical)

    def test_runtime_service_paths_are_present(self) -> None:
        missing = [str(path.relative_to(ROOT)) for path in RUNTIME_PATHS if not path.is_file()]
        self.assertEqual(missing, [])

    def test_composed_skills_are_present_and_thin(self) -> None:
        missing = [str(path.relative_to(ROOT)) for path in COMPOSED_SKILLS if not path.is_file()]
        self.assertEqual(missing, [])
        for path in COMPOSED_SKILLS:
            content = path.read_text(encoding="utf-8")
            self.assertIn("canonical", content.lower(), str(path))
            self.assertTrue(any(token in content for token in ("evidence", "verification", "provenance")), str(path))

    def test_interactive_renderer_is_standalone(self) -> None:
        renderer = ROOT / "skills" / "engineering" / "interactive-documentation" / "render_document.py"
        self.assertTrue(renderer.is_file())
        sample = {
            "title": "Test map",
            "repository": "test",
            "snapshot_digest": "abc",
            "generated_at": "2026-09-16T00:00:00Z",
            "nodes": [
                {"id": "a", "label": "A", "kind": "service", "sources": ["a.py::A"], "evidence_ids": ["ev-1"]},
                {"id": "b", "label": "B", "kind": "store", "sources": ["b.py::B"], "evidence_ids": ["ev-2"]},
            ],
            "edges": [{"source": "a", "target": "b", "label": "writes"}],
            "views": [{"id": "overview", "label": "Overview", "nodes": ["a", "b"]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "map.json"
            output = Path(tmp) / "map.html"
            source.write_text(json.dumps(sample), encoding="utf-8")
            subprocess.run([sys.executable, str(renderer), str(source), str(output)], check=True)
            html = output.read_text(encoding="utf-8")
        self.assertIn("Interactive system map", html)
        self.assertIn("Snapshot", html)
        self.assertNotIn("cdn.", html.lower())
        self.assertNotIn("https://", html.lower())

    def test_plugin_versions_are_aligned(self) -> None:
        canonical = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(canonical, "1.0.4")
        self.assertEqual(plugin["version"], canonical)
        self.assertEqual(marketplace["version"], canonical)
        self.assertEqual(marketplace["plugins"][0]["version"], canonical)

    def test_artifact_contract_matches_explicit_install_semantics(self) -> None:
        contract = json.loads((ROOT / ".ai-harness" / "ARTIFACT_UPGRADE_CONTRACT.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["downgrade"], "explicit_install_only")
        self.assertEqual(contract["automatic_update_downgrade"], "forbidden")


if __name__ == "__main__":
    unittest.main()
