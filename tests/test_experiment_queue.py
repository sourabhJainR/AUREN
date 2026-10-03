import tempfile
import unittest
from pathlib import Path

from portable.autonomy_curriculum import CurriculumObjective
from portable.curriculum_experiment import CurriculumExperimentController
from portable.experiment_queue import AutonomousExperimentQueue


class ExperimentQueueTests(unittest.TestCase):
    def make_experiment(self, root):
        return CurriculumExperimentController(root).plan(
            CurriculumObjective("goal_completion", "curriculum:goal_completion", .75, .60, .15, .2, "gap")
        )

    def test_enqueue_is_idempotent_and_selects_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exp = self.make_experiment(root)
            q = AutonomousExperimentQueue(root)
            q.enqueue(exp)
            q.enqueue(exp)
            state = q.next()
            self.assertIsNotNone(state)
            self.assertEqual(state.experiment.experiment_id, exp.experiment_id)
            self.assertFalse(state.completed)

    def test_requires_two_independent_cohorts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exp = self.make_experiment(root)
            q = AutonomousExperimentQueue(root, minimum_samples_per_cohort=2)
            q.enqueue(exp)
            for episode, cohort in (("c1", "control"), ("c2", "control"), ("t1", "treatment"), ("t2", "treatment")):
                q.mark_observed(exp.experiment_id, {
                    "episode_id": episode, "cohort": cohort, "metric": .80,
                    "evidence_ids": [episode], "attributable": True,
                })
            state = q.next()
            self.assertIsNone(state)
            rows = q._states()
            self.assertTrue(rows[0].completed)
            self.assertEqual(rows[0].control_samples, 2)
            self.assertEqual(rows[0].treatment_samples, 2)

    def test_non_attributable_observation_does_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exp = self.make_experiment(root)
            q = AutonomousExperimentQueue(root)
            q.enqueue(exp)
            q.mark_observed(exp.experiment_id, {
                "episode_id": "c1", "cohort": "control", "metric": .80,
                "evidence_ids": ["e1"], "attributable": False,
            })
            state = q.next()
            self.assertIsNotNone(state)
            self.assertEqual(state.control_samples, 0)

if __name__ == "__main__":
    unittest.main()
