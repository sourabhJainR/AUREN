import tempfile
import unittest
from pathlib import Path

from portable.autonomy_curriculum import CurriculumObjective
from portable.curriculum_experiment import CurriculumExperimentController


class CurriculumExperimentTests(unittest.TestCase):
    def objective(self, *, current=0.60, target=0.75):
        return CurriculumObjective(
            dimension="cross_task_transfer",
            objective_id="curriculum:cross_task_transfer",
            target=target,
            current=current,
            gap=max(0.0, target-current),
            priority=0.9,
            rationale="test objective",
        )

    def test_plan_is_deterministic_and_requires_holdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = CurriculumExperimentController(Path(tmp))
            first = controller.plan(self.objective())
            second = controller.plan(self.objective())
            self.assertEqual(first.experiment_id, second.experiment_id)
            self.assertTrue(first.holdout_required)
            self.assertEqual(first.dimension, "cross_task_transfer")
            self.assertEqual(first.baseline_metric, 0.60)

    def test_close_requires_evidence_and_records_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = CurriculumExperimentController(root)
            experiment = controller.plan(self.objective())
            outcome = controller.close(
                experiment,
                benchmark_after=0.65,
                evidence_ids=("holdout:a", "holdout:b"),
            )
            self.assertTrue(outcome.success)
            self.assertAlmostEqual(outcome.delta, 0.05)
            self.assertEqual(outcome.evidence_ids, ("holdout:a", "holdout:b"))
            self.assertTrue(list(root.rglob("*.jsonl")))

    def test_close_rejects_missing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = CurriculumExperimentController(Path(tmp))
            experiment = controller.plan(self.objective())
            with self.assertRaises(ValueError):
                controller.close(experiment, benchmark_after=0.70, evidence_ids=())


if __name__ == "__main__":
    unittest.main()
