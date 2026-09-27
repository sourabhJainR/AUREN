import tempfile
import unittest
from pathlib import Path

from portable.repository_engineering_cycle import PatchProposal, RepositoryEngineeringCycle
from portable.sandboxed_repository import CommandSpec


class RepositoryEngineeringCycleTests(unittest.TestCase):
    def test_verified_patch_runs_in_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "sample.py"
            target.write_text("print('old')\n")
            before = target.read_text()
            cycle = RepositoryEngineeringCycle(root)
            result = cycle.run(
                PatchProposal("change sample", {"sample.py": "print('new')\n"}, model="test"),
                commands=(CommandSpec("run", ("python", "sample.py")),),
            )
            self.assertTrue(result.accepted)
            self.assertIn("sample.py", result.execution.changed_files)
            self.assertEqual(before, target.read_text())

    def test_unsafe_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            cycle = RepositoryEngineeringCycle(Path(d))
            with self.assertRaises(PermissionError):
                cycle.run(
                    PatchProposal("escape", {"../bad.py": "x"}),
                    commands=(CommandSpec("run", ("python", "-c", "print(1)")),),
                )

    def test_failed_verification_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sample.py").write_text("raise SystemExit(4)\n")
            result = RepositoryEngineeringCycle(root).run(
                PatchProposal("broken", {"sample.py": "raise SystemExit(4)\n"}),
                commands=(CommandSpec("run", ("python", "sample.py")),),
            )
            self.assertFalse(result.accepted)
            self.assertEqual(result.execution.failure, "run")


if __name__ == "__main__":
    unittest.main()
