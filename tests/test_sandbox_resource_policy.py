from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / ".ai-harness" / "runtime" / "sandbox.py"
SPEC = importlib.util.spec_from_file_location("auren_harness_sandbox_under_test", MODULE)
assert SPEC is not None and SPEC.loader is not None
sandbox = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sandbox
SPEC.loader.exec_module(sandbox)


class SandboxResourcePolicyTests(unittest.TestCase):
    def test_invalid_resource_budgets_fail_before_command_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            for kwargs in (
                {"max_memory_mb": 0},
                {"max_memory_mb": True},
                {"max_processes": 0},
                {"max_processes": False},
                {"max_output_chars": 0},
                {"timeout_seconds": 0},
            ):
                with self.subTest(kwargs=kwargs), self.assertRaises(sandbox.SandboxViolation):
                    sandbox.run(
                        [os.sys.executable, "-c", "raise SystemExit(99)"],
                        workspace=workspace,
                        policy=sandbox.SandboxPolicy(**kwargs),
                    )

    @unittest.skipIf(os.name == "nt", "POSIX RLIMIT_AS semantics")
    def test_memory_budget_is_applied_to_child(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            policy = sandbox.SandboxPolicy(max_memory_mb=512, timeout_seconds=5)
            result = sandbox.run(
                [os.sys.executable, "-c", "import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0])"],
                workspace=workspace,
                policy=policy,
            )
            self.assertEqual(0, result.returncode)
            self.assertEqual(str(512 * 1024 * 1024) + "\n", result.stdout)

    def test_output_capture_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            result = sandbox.run(
                [os.sys.executable, "-c", "print('x' * 10000)"],
                workspace=Path(temp),
                policy=sandbox.SandboxPolicy(max_output_chars=128, timeout_seconds=5),
            )
            self.assertLessEqual(len(result.stdout.encode("utf-8")), 128)
            self.assertLessEqual(len(result.stderr.encode("utf-8")), 128)


if __name__ == "__main__":
    unittest.main()
