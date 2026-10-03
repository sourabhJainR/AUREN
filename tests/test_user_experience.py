import json
import os
import tempfile
import unittest
from pathlib import Path

from portable.user_experience import build_status, render_json, run_demo, run_doctor


class UserExperienceTests(unittest.TestCase):
    def test_doctor_reports_uninstalled_auren_as_warning_not_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            (project / ".git").mkdir()
            checks, exit_code = run_doctor(project, auren_home=Path(temp) / ".auren")
            self.assertEqual(exit_code, 0)
            self.assertTrue(any(c.name == "installation" and c.status == "WARN" for c in checks))

    def test_status_is_read_only_when_auren_home_does_not_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            auren_home = Path(temp) / ".auren"
            snapshot = build_status(auren_home=auren_home, project_root=Path(temp))
            self.assertFalse(auren_home.exists())
            self.assertEqual(snapshot["installation"]["installed"], False)

    def test_json_render_is_deterministic_and_structured(self):
        payload = {"z": 1, "a": {"b": True}}
        self.assertEqual(render_json(payload), '{\n  "a": {\n    "b": true\n  },\n  "z": 1\n}')

    def test_demo_returns_repository_evidence_without_mutating_project(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            (project / "app.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
            before = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))
            result = run_demo(project, token_budget=300)
            after = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))
            self.assertEqual(before, after)
            self.assertTrue(result["snapshot"])
            self.assertIn("metrics", result)
            self.assertIn("files", result)

    def test_status_exposes_provider_and_skill_readiness(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = build_status(auren_home=Path(temp) / ".auren", project_root=Path(temp))
            self.assertIn("providers", snapshot)
            self.assertIn("skills", snapshot)
            self.assertEqual(set(snapshot["providers"]), {"claude", "codex", "gemini"})
            self.assertEqual(set(snapshot["skills"]), {"agents", "claude", "gemini"})


if __name__ == "__main__":
    unittest.main()
