import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from portable.context_specific_learning import ContextSpecificDecisionLearner
from portable.decision_context import DecisionContext


class ContextSpecificDecisionLearnerTests(unittest.TestCase):
    def test_cold_start_keeps_baseline(self):
        with TemporaryDirectory() as d:
            learner = ContextSpecificDecisionLearner(Path(d))
            result = learner.select(
                role="team", task="task",
                context=DecisionContext(.5, .5, .2, .2, .8, .3))
            self.assertFalse(result.learned)
            self.assertEqual((result.strategy, result.mode), ("default", "balanced"))

    def test_exact_context_evidence_isolated(self):
        with TemporaryDirectory() as d:
            learner = ContextSpecificDecisionLearner(Path(d), minimum_samples=3)
            context = DecisionContext(.8, .8, .1, .2, .9, .8)
            other = DecisionContext(.1, .1, .9, .8, .2, .1)
            for i in range(6):
                learner.record(
                    role="team", task="task", context=context,
                    strategy="deep-verify", mode="serial", outcome="passed",
                    evidence_quality=.95, cost_score=.1, duration_seconds=2,
                    evidence_ids=[f"e:{i}"])
            result = learner.select(role="team", task="task", context=context)
            self.assertTrue(result.learned)
            self.assertEqual((result.strategy, result.mode), ("deep-verify", "serial"))
            cold = learner.select(role="team", task="task", context=other)
            self.assertFalse(cold.learned)

    def test_margin_prevents_weak_context_reuse(self):
        with TemporaryDirectory() as d:
            learner = ContextSpecificDecisionLearner(Path(d), minimum_samples=3, min_margin=.20)
            context = DecisionContext(.5, .5, .5, .5, .5, .5)
            for i in range(3):
                learner.record(
                    role="team", task="task", context=context,
                    strategy="fast-path", mode="parallel", outcome="passed",
                    evidence_quality=.70, cost_score=.40, duration_seconds=2,
                    evidence_ids=[f"e:{i}"])
            result = learner.select(role="team", task="task", context=context)
            self.assertFalse(result.learned)
            self.assertEqual((result.strategy, result.mode), ("default", "balanced"))


if __name__ == "__main__":
    unittest.main()
