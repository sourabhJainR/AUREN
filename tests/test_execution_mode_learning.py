import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from portable.execution_mode_learning import ExecutionModeLearner, execution_mode


class ExecutionModeLearningTests(unittest.TestCase):
    def test_cold_start_keeps_baseline(self):
        with TemporaryDirectory() as directory:
            choice = ExecutionModeLearner(Path(directory)).select(
                role="team", task="artifact", baseline="balanced")
            self.assertEqual(choice.mode.name, "balanced")
            self.assertFalse(choice.learned)

    def test_repeated_evidence_selects_mode(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            learner = ExecutionModeLearner(root)
            for i in range(6):
                learner.record(
                    role="team", task="artifact", mode="serial",
                    outcome="passed", evidence_quality=.95, cost_score=.2,
                    duration_seconds=4, verification="deep", retry="retry",
                    resource_fraction=.7, parallelism=1, evidence_ids=[f"mode:{i}"])
            choice = learner.select(role="team", task="artifact", baseline="balanced")
            self.assertEqual(choice.mode.name, "serial")
            self.assertTrue(choice.learned)
            self.assertEqual(choice.mode.max_parallelism, 1)
            self.assertEqual(choice.mode.verification_depth, "deep")

    def test_unknown_mode_falls_back_safely(self):
        self.assertEqual(execution_mode("not-real").name, "balanced")


if __name__ == "__main__":
    unittest.main()
