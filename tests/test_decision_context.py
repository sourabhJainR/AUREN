import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from portable.counterfactual_decision_fabric import CounterfactualDecisionFabric
from portable.decision_context import DecisionContext, build_decision_context, context_adjustment
from portable.execution_mode_learning import ExecutionModeLearner
from portable.execution_strategy_learning import ExecutionStrategyLearner


class DecisionContextTests(unittest.TestCase):
    def _agents(self):
        class Agent:
            def __init__(self, name, deps=(), read_only=True, critical=True, duration=30, evidence=.7):
                self.name, self.depends_on = name, deps
                self.read_only, self.critical = read_only, critical
                self.estimated_duration_seconds, self.evidence_value = duration, evidence
        return (
            Agent("planner"),
            Agent("builder", ("planner",), read_only=False, duration=120, evidence=.9),
            Agent("verifier", ("builder",), duration=90, evidence=.95),
        )

    def test_profile_is_bounded_and_stable(self):
        context = build_decision_context(
            task="implement adaptive execution",
            agents=self._agents(),
            pressure={"cpu_pressure": .2, "queue_pressure": .1, "memory_pressure": .3},
            timeout_seconds=300,
        )
        self.assertEqual(len(context.fingerprint), 16)
        self.assertTrue(all(0.0 <= float(getattr(context, name)) <= 1.0 for name in (
            "complexity", "dependency_parallelism", "resource_pressure",
            "latency_pressure", "evidence_value", "failure_risk")))
        self.assertEqual(context.fingerprint, context.fingerprint)

    def test_context_changes_policy_preference_without_overriding_evidence(self):
        high_pressure = DecisionContext(.6, .8, .95, .2, .7, .4)
        low_pressure = DecisionContext(.6, .8, .1, .2, .7, .4)
        self.assertGreater(
            context_adjustment("default", "parallel", low_pressure),
            context_adjustment("default", "parallel", high_pressure),
        )

    def test_counterfactual_exposes_context(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = DecisionContext(.5, .5, .2, .2, .8, .3)
            result = CounterfactualDecisionFabric(root).evaluate(
                role="team", task="artifact", context=context)
            self.assertFalse(result["changed"])
            self.assertEqual(result["context"]["fingerprint"], context.fingerprint)

    def test_existing_evidence_selection_remains_valid(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            modes = ExecutionModeLearner(root)
            strategies = ExecutionStrategyLearner(root)
            for i in range(6):
                modes.record(role="team", task="artifact", mode="serial", outcome="passed",
                             evidence_quality=.95, cost_score=.15, duration_seconds=2,
                             verification="deep", retry="retry", resource_fraction=.7,
                             parallelism=1, evidence_ids=[f"m:{i}"])
                strategies.record(role="team", task="artifact", strategy="evidence-first",
                                  outcome="passed", evidence_quality=.95, cost_score=.15,
                                  duration_seconds=2, verification="deep", retry="retry",
                                  evidence_ids=[f"s:{i}"])
            result = CounterfactualDecisionFabric(root).evaluate(
                role="team", task="artifact",
                context=DecisionContext(.7, .2, .1, .1, .95, .7))
            self.assertTrue(result["changed"])
            self.assertEqual(result["selected"]["strategy"], "evidence-first")
            self.assertEqual(result["selected"]["mode"], "serial")


if __name__ == "__main__":
    unittest.main()
