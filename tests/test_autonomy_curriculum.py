import unittest
from portable.autonomy_curriculum import AutonomyCurriculumController


class AutonomyCurriculumTests(unittest.TestCase):
    def test_selects_benchmark_gaps(self):
        c = AutonomyCurriculumController(target=.75, max_objectives=2)
        out = c.propose({
            "goal_completion": .9,
            "cross_task_transfer": .4,
            "self_model_calibration": .6,
            "causal_learning": .7,
            "safe_autonomy": .9,
            "resource_efficiency": .8,
        })
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].dimension, "cross_task_transfer")
        self.assertGreater(out[0].priority, out[1].priority)

    def test_no_gap_means_no_curriculum(self):
        c = AutonomyCurriculumController()
        self.assertEqual(c.propose({name: .9 for name in (
            "goal_completion", "cross_task_transfer", "self_model_calibration",
            "causal_learning", "safe_autonomy", "resource_efficiency"
        )}), ())


if __name__ == "__main__":
    unittest.main()
