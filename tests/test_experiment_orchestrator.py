import tempfile
import unittest
from pathlib import Path

from portable.curriculum_experiment import CurriculumExperimentController
from portable.autonomy_curriculum import CurriculumObjective
from portable.experiment_orchestrator import ClosedLoopExperimentOrchestrator


class ClosedLoopExperimentTests(unittest.TestCase):
    def experiment(self):
        with tempfile.TemporaryDirectory() as tmp:
            return CurriculumExperimentController(Path(tmp)).plan(
                CurriculumObjective("goal_completion", "curriculum:goal_completion", .75, .60, .15, .2, "gap")
            )

    def test_assignment_is_deterministic_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experiment = CurriculumExperimentController(root).plan(
                CurriculumObjective("goal_completion", "curriculum:goal_completion", .75, .60, .15, .2, "gap")
            )
            controller = ClosedLoopExperimentOrchestrator(root)
            a = controller.assign(experiment, episode_id="episode-1")
            b = controller.assign(experiment, episode_id="episode-1")
            self.assertEqual(a, b)
            self.assertLessEqual(a.risk_budget, .25)
            self.assertLessEqual(a.resource_budget, .50)

    def test_control_has_no_intervention_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experiment = CurriculumExperimentController(root).plan(
                CurriculumObjective("goal_completion", "curriculum:goal_completion", .75, .60, .15, .2, "gap")
            )
            assignment = ClosedLoopExperimentOrchestrator(root).assign(
                experiment, episode_id="episode-control", preferred_cohort="control"
            )
            self.assertEqual(assignment.cohort, "control")
            self.assertFalse(assignment.intervention_allowed)

    def test_missing_holdout_is_not_attributable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experiment = CurriculumExperimentController(root).plan(
                CurriculumObjective("goal_completion", "curriculum:goal_completion", .75, .60, .15, .2, "gap"
            ))
            controller = ClosedLoopExperimentOrchestrator(root)
            assignment = controller.assign(experiment, episode_id="episode-1", preferred_cohort="treatment")
            observed = controller.observe(assignment, metric=.80, evidence_ids=("e1",), holdout=False)
            self.assertFalse(observed.attributable)

    def test_observation_persists_only_with_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experiment = CurriculumExperimentController(root).plan(
                CurriculumObjective("goal_completion", "curriculum:goal_completion", .75, .60, .15, .2, "gap")
            )
            controller = ClosedLoopExperimentOrchestrator(root)
            assignment = controller.assign(experiment, episode_id="episode-1", preferred_cohort="treatment")
            observed = controller.observe(assignment, metric=.80, evidence_ids=("e1",), holdout=True)
            self.assertTrue(observed.attributable)
            self.assertEqual(observed.evidence_ids, ("e1",))

    def test_requires_both_cohorts_for_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experiment = CurriculumExperimentController(root).plan(
                CurriculumObjective("goal_completion", "curriculum:goal_completion", .75, .60, .15, .2, "gap")
            )
            controller = ClosedLoopExperimentOrchestrator(root)
            c = controller.assign(experiment, episode_id="c", preferred_cohort="control")
            t = controller.assign(experiment, episode_id="t", preferred_cohort="treatment")
            self.assertTrue(controller.control_and_treatment_ready((c, t)))
