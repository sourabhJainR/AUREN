import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from portable.counterfactual_decision_fabric import CounterfactualDecisionFabric
from portable.execution_mode_learning import ExecutionModeLearner
from portable.execution_strategy_learning import ExecutionStrategyLearner


class CounterfactualDecisionFabricTests(unittest.TestCase):
    def _seed(self, root: Path) -> None:
        modes = ExecutionModeLearner(root)
        strategies = ExecutionStrategyLearner(root)
        for i in range(6):
            modes.record(
                role="team", task="artifact", mode="serial", outcome="passed",
                evidence_quality=.95, cost_score=.15, duration_seconds=2,
                verification="deep", retry="retry", resource_fraction=.7,
                parallelism=1, evidence_ids=[f"m:{i}"])
            strategies.record(
                role="team", task="artifact", strategy="evidence-first",
                outcome="passed", evidence_quality=.95, cost_score=.15,
                duration_seconds=2, verification="deep", retry="retry",
                evidence_ids=[f"s:{i}"])

    def test_cold_start_is_advisory(self):
        with TemporaryDirectory() as directory:
            result = CounterfactualDecisionFabric(Path(directory)).evaluate(
                role="team", task="artifact")
            self.assertFalse(result["changed"])
            self.assertEqual(result["selected"]["strategy"], "default")

    def test_composes_independent_evidence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._seed(root)
            result = CounterfactualDecisionFabric(root).evaluate(
                role="team", task="artifact")
            self.assertTrue(result["changed"])
            self.assertEqual(result["selected"]["strategy"], "evidence-first")
            self.assertEqual(result["selected"]["mode"], "serial")


if __name__ == "__main__":
    unittest.main()
