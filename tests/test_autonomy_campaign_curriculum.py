import unittest
from portable.autonomy_benchmark import AutonomyBenchmarkGate
from portable.autonomy_benchmark_campaign import AutonomyBenchmarkCampaignRunner, CampaignEpisode
from portable.autonomy_campaign_curriculum import AutonomyCampaignCurriculum


class AutonomyCampaignCurriculumTests(unittest.TestCase):
    def episode(self, eid, domain, holdout):
        score = .9
        return CampaignEpisode(eid, domain, holdout, {
            "goal_completion": score, "cross_task_transfer": score,
            "self_model_calibration": score, "causal_learning": score,
            "safe_autonomy": score, "resource_efficiency": score,
        }, (eid + "-evidence",))

    def test_proposes_unseen_domain(self):
        campaign = AutonomyBenchmarkCampaignRunner().evaluate((
            self.episode("e1", "coding", True),
            self.episode("e2", "research", False),
            self.episode("e3", "planning", False),
            self.episode("e4", "debugging", False),
        ))
        objectives = AutonomyCampaignCurriculum().propose(campaign)
        self.assertTrue(objectives)
        self.assertEqual(objectives[0].domain, "analysis")
        self.assertTrue(objectives[0].holdout)

    def test_proposes_missing_holdout_before_declaring_breadth_complete(self):
        campaign = AutonomyBenchmarkCampaignRunner().evaluate((
            self.episode("e1", "coding", False),
            self.episode("e2", "research", False),
            self.episode("e3", "planning", True),
            self.episode("e4", "debugging", True),
        ))
        objectives = AutonomyCampaignCurriculum().propose(campaign)
        self.assertTrue(objectives)
        self.assertIn("coding", {x.domain for x in objectives})

if __name__ == "__main__":
    unittest.main()
