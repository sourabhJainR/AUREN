from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".ai-harness" / "runtime"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, RUNTIME / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses and other runtime introspection expect the executing module
    # to be registered in sys.modules when using a file-based loader.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


class CoreRuntimeServiceTests(unittest.TestCase):
    def test_compaction_preserves_acceptance_and_bounds_output(self):
        module = load("auto_compaction")
        text = "# GOAL\nKeep this goal.\n# ACCEPTANCE\nMust pass.\n# BODY\n" + ("repeated engineering detail " * 1000)
        result = module.compact(text, budget_chars=2000)
        self.assertTrue(result.compacted)
        self.assertLessEqual(len(result.text), 2000)
        self.assertIn("Keep this goal", result.text)
        self.assertIn("Must pass", result.text)

    def test_feedback_requires_repeated_verified_success(self):
        module = load("feedback_loop")
        with tempfile.TemporaryDirectory() as temp:
            loop = module.FeedbackLoop(Path(temp))
            for index in range(5):
                loop.observe(task_id=str(index), outcome="success", verified=True, strategy="s1", evidence=["e"])
            result = loop.evaluate(strategy="s1")
            self.assertTrue(result["candidate_eligible"])
            candidate = loop.candidate(strategy="s1", proposed_change="candidate")
            self.assertFalse(candidate["active"])

    def test_sandbox_rejects_shell_destruction_pattern(self):
        module = load("sandbox")
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(module.SandboxViolation):
                module.run(["rm", "-rf", "tmp"], workspace=Path(temp))


if __name__ == "__main__":
    unittest.main()
