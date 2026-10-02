import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from portable.execution_mode_canary import ExecutionModeCanaryController
from portable.execution_mode_learning import ExecutionModeLearner


class ExecutionModeCanaryTests(unittest.TestCase):
    def _seed(self, root: Path, outcome: str = "passed") -> None:
        learner = ExecutionModeLearner(root)
        for i in range(6):
            learner.record(
                role="team", task="artifact", mode="serial", outcome=outcome,
                evidence_quality=.9 if outcome == "passed" else .2,
                cost_score=.2, duration_seconds=2, verification="deep",
                retry="retry", resource_fraction=.7, parallelism=1,
                evidence_ids=[f"mode:{i}"])

    def test_canary_promotes_only_on_next_run(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._seed(root)
            controller = ExecutionModeCanaryController(root)
            first = controller.evaluate(role="team", task="artifact", mode="serial")
            self.assertEqual(first.state, "canary")
            controller.record_state(root, first)
            second = controller.evaluate(role="team", task="artifact", mode="serial")
            self.assertEqual(second.state, "promoted")

    def test_failed_canary_rolls_back(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._seed(root, outcome="failed")
            controller = ExecutionModeCanaryController(root)
            first = controller.evaluate(role="team", task="artifact", mode="serial")
            controller.record_state(root, first)
            second = controller.evaluate(role="team", task="artifact", mode="serial")
            self.assertEqual(second.state, "rollback")


if __name__ == "__main__":
    unittest.main()
