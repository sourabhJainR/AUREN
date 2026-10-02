import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from portable.execution_strategy import execution_strategy
from portable.execution_strategy_learning import ExecutionStrategyLearner


class ExecutionStrategyLearningTests(unittest.TestCase):
    def test_cold_start_keeps_baseline(self):
        with TemporaryDirectory() as directory:
            choice = ExecutionStrategyLearner(Path(directory)).select(
                role="team", task="check artifact", baseline="default")
            self.assertEqual(choice.strategy.name, "default")
            self.assertFalse(choice.learned)

    def test_repeated_evidence_can_select_strategy(self):
        with TemporaryDirectory() as directory:
            learner = ExecutionStrategyLearner(Path(directory))
            for _ in range(5):
                learner.record(
                    role="team", task="check artifact", strategy="evidence-first",
                    outcome="passed", evidence_quality=0.95, cost_score=0.2,
                    duration_seconds=10, verification="deep", retry="stop",
                    evidence_ids=[f"test:evidence:{_}"])
            choice = learner.select(role="team", task="check artifact", baseline="default")
            self.assertEqual(choice.strategy.name, "evidence-first")
            self.assertTrue(choice.learned)

    def test_unknown_strategy_falls_back_safely(self):
        self.assertEqual(execution_strategy("not-real").name, "default")


if __name__ == "__main__":
    unittest.main()
