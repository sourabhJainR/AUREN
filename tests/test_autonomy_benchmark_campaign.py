import unittest
from portable.autonomy_benchmark_campaign import AutonomyBenchmarkCampaignRunner, CampaignEpisode


class AutonomyBenchmarkCampaignTests(unittest.TestCase):
    def episode(self, eid, domain, holdout, score=.9):
        return CampaignEpisode(eid, domain, holdout, {
            "goal_completion": score,
            "cross_task_transfer": score,
            "self_model_calibration": score,
            "causal_learning": score,
            "safe_autonomy": score,
            "resource_efficiency": score,
        }, (eid + "-evidence",))

    def test_single_episode_cannot_pass_breadth_gate(self):
        result = AutonomyBenchmarkCampaignRunner().evaluate((self.episode("e1", "coding", True),))
        self.assertFalse(result.breadth_passed)
        self.assertFalse(result.gate_passed)

    def test_requires_independent_holdout_domains(self):
        rows = (
            self.episode("e1", "coding", False),
            self.episode("e2", "research", False),
            self.episode("e3", "planning", True),
            self.episode("e4", "debugging", True),
        )
        result = AutonomyBenchmarkCampaignRunner().evaluate(rows)
        self.assertTrue(result.breadth_passed)
        self.assertTrue(result.gate_passed)

    def test_duplicate_evidence_fails_closed(self):
        rows = (
            CampaignEpisode("e1", "coding", True, {"goal_completion": .9}, ("same",)),
            CampaignEpisode("e2", "research", True, {"goal_completion": .9}, ("same",)),
            self.episode("e3", "planning", True),
            self.episode("e4", "debugging", True),
        )
        result = AutonomyBenchmarkCampaignRunner().evaluate(rows)
        self.assertFalse(result.independent)
        self.assertFalse(result.gate_passed)

if __name__ == "__main__":
    unittest.main()
