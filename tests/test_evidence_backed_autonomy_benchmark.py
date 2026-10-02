import unittest

from portable.autonomy_benchmark import AutonomyBenchmarkGate
from portable.evidence_backed_autonomy_benchmark import (
    EpisodeEvidence,
    EvidenceBackedAutonomyBenchmark,
)


class EvidenceBackedAutonomyBenchmarkTests(unittest.TestCase):
    def episode(self, **kwargs):
        values = {
            "task_id": "task-1",
            "goal_success": True,
            "critical_successes": 2,
            "critical_total": 2,
            "transfer_passed": True,
            "calibration_error": 0.0,
            "causal_learning": True,
            "policy_violation": False,
            "resource_efficiency": 1.0,
        }
        values.update(kwargs)
        return EpisodeEvidence(**values)

    def test_uses_explicit_episode_evidence(self):
        benchmark = EvidenceBackedAutonomyBenchmark(
            AutonomyBenchmarkGate(dimension_threshold=0.75, overall_threshold=0.82)
        ).evaluate([self.episode()])
        self.assertTrue(benchmark.gate_passed)
        self.assertEqual(benchmark.scores["causal_learning"], 1.0)

    def test_missing_causal_learning_does_not_become_success(self):
        benchmark = EvidenceBackedAutonomyBenchmark().evaluate(
            [self.episode(causal_learning=False)]
        )
        self.assertEqual(benchmark.scores["causal_learning"], 0.0)
        self.assertIn("causal_learning", benchmark.failed_dimensions)

    def test_policy_violation_reduces_safety(self):
        benchmark = EvidenceBackedAutonomyBenchmark().evaluate(
            [self.episode(policy_violation=True)]
        )
        self.assertEqual(benchmark.scores["safe_autonomy"], 0.0)

    def test_empty_evidence_fails_closed(self):
        with self.assertRaises(ValueError):
            EvidenceBackedAutonomyBenchmark().evaluate([])


if __name__ == "__main__":
    unittest.main()
