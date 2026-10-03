import unittest

from portable.autonomous_campaign_learning import CampaignAttribution, CampaignIntervention, CampaignLearning
from portable.autonomous_holdout_retest import AutonomousHoldoutRetestPlanner


class AutonomousHoldoutRetestTests(unittest.TestCase):
    def _learning(self):
        attribution = CampaignAttribution(
            "task:failed", True, False, .9, .2, .7, "capability", .9,
            ("evidence:failed",), "verified execution did not meet prediction",
        )
        intervention = CampaignIntervention(
            "task:failed", "capability",
            "probe a missing capability on an independent holdout", True,
            "intervention must be validated on an independent holdout",
        )
        return CampaignLearning(
            "campaign-1", (attribution,), (intervention,),
            ("task:failed",), ("task:failed",), None,
        )

    def test_planner_creates_new_holdout_contract(self):
        plans = AutonomousHoldoutRetestPlanner(max_retests=2).plan(
            self._learning(), available_domains=("coding", "research", "analysis")
        )
        self.assertEqual(len(plans), 1)
        self.assertTrue(plans[0].contract.holdout)
        self.assertNotEqual(plans[0].contract.task_id, "task:failed")

    def test_planner_is_bounded_and_deduplicated(self):
        learning = self._learning()
        plans = AutonomousHoldoutRetestPlanner(max_retests=1).plan(
            learning,
            available_domains=("coding", "research", "analysis"),
            existing_task_ids=("benchmark:research:already-used",),
        )
        self.assertLessEqual(len(plans), 1)
        self.assertEqual(len({p.contract.task_id for p in plans}), len(plans))

    def test_no_domains_means_no_execution_proposal(self):
        self.assertEqual(
            AutonomousHoldoutRetestPlanner().plan(self._learning(), available_domains=()),
            (),
        )


if __name__ == "__main__":
    unittest.main()
