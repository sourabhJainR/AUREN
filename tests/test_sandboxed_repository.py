import tempfile
import unittest
from pathlib import Path

from portable.sandboxed_repository import CommandSpec, SandboxedRepository


class SandboxedRepositoryTests(unittest.TestCase):
    def test_inspection_and_successful_execution(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sample.py").write_text("print('ok')\n")
            repo = SandboxedRepository(root)
            inspection = repo.inspect()
            self.assertIn("sample.py", inspection.files)
            result = repo.execute((CommandSpec("run", ("python", "sample.py")),))
            self.assertTrue(result.passed)
            self.assertTrue(result.evidence_ids)

    def test_command_allowlist_and_no_source_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "sample.py"
            target.write_text("print('ok')\n")
            before = target.read_bytes()
            repo = SandboxedRepository(root)
            with self.assertRaises(PermissionError):
                repo.execute((CommandSpec("shell", ("bash", "-c", "echo bad")),))
            self.assertEqual(before, target.read_bytes())

    def test_failure_is_captured_and_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sample.py").write_text("raise SystemExit(3)\n")
            result = SandboxedRepository(root).execute(
                (CommandSpec("run", ("python", "sample.py"), timeout_seconds=5),)
            )
            self.assertFalse(result.passed)
            self.assertEqual(result.failure, "run")
            self.assertEqual(result.commands[0].return_code, 3)


if __name__ == "__main__":
    unittest.main()
