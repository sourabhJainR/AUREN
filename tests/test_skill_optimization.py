import tempfile
import unittest
from pathlib import Path

from portable.persistent_memory import PersistentMemory
from portable.skill_optimization import SkillEdit, SkillOptimizer, SkillScore


class SkillOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.memory = PersistentMemory(Path(self.tmp.name) / "memory.db", require_approval=False)
        self.optimizer = SkillOptimizer(self.memory, "demo", metric="hard")

    def tearDown(self):
        self.tmp.cleanup()

    def test_disjoint_holdout_and_strict_improvement_accepts(self):
        calls = []

        def score(skill, task_ids):
            calls.append((skill, tuple(task_ids)))
            return SkillScore(1.0 if "retry" in skill else 0.5)

        result = self.optimizer.epoch(
            task_family="coding",
            skill="# Skill",
            proposals=(SkillEdit("add", content="Always retry once.", rationale="verified failures"),),
            train_ids=("t1", "t2"),
            holdout_ids=("v1", "v2"),
            evidence_ids=("ev1", "ev2"),
            score=score,
        )
        self.assertTrue(result.accepted)
        self.assertIn("retry", result.skill)
        self.assertEqual(result.rejected_edits, ())
        self.assertEqual(result.holdout_ids, ("v1", "v2"))
        self.assertEqual(calls[0][1], ("v1", "v2"))

    def test_regression_is_rejected_and_remembered(self):
        def score(skill, task_ids):
            return SkillScore(0.4 if "bad" in skill else 0.8)

        result = self.optimizer.epoch(
            task_family="coding",
            skill="# Skill",
            proposals=(SkillEdit("add", content="bad rule", rationale="candidate"),),
            train_ids=("t1",),
            holdout_ids=("v1",),
            evidence_ids=("ev1",),
            score=score,
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.skill, "# Skill")
        self.assertEqual(self.optimizer.rejected()[0].content, "bad rule")

    def test_overlap_is_fail_closed(self):
        with self.assertRaises(ValueError):
            self.optimizer.epoch(
                task_family="coding",
                skill="# Skill",
                proposals=(),
                train_ids=("same",),
                holdout_ids=("same",),
                evidence_ids=("ev1",),
                score=lambda skill, ids: SkillScore(0.5),
            )

    def test_unmatched_edit_is_visible(self):
        result = self.optimizer.epoch(
            task_family="coding",
            skill="# Skill",
            proposals=(SkillEdit("replace", content="new", anchor="missing"),),
            train_ids=("t1",),
            holdout_ids=("v1",),
            evidence_ids=("ev1",),
            score=lambda skill, ids: SkillScore(0.5),
        )
        self.assertFalse(result.accepted)
        self.assertEqual(len(result.unmatched_edits), 1)
        self.assertEqual(self.optimizer.rejected(), ())

    def test_adaptive_learning_owns_the_maintenance_entrypoint(self):
        from portable.adaptive_learning import AdaptiveLearningStore

        store = AdaptiveLearningStore(self.memory, "demo")
        result = store.optimize_skill(
            task_family="coding",
            skill="# Skill",
            proposals=(SkillEdit("add", content="Use evidence."),),
            train_ids=("t1",),
            holdout_ids=("v1",),
            score=lambda skill, ids: SkillScore(0.9 if "evidence" in skill else 0.5),
        )
        self.assertTrue(result.accepted)

    def test_missing_evidence_is_fail_closed(self):
        with self.assertRaises(ValueError):
            self.optimizer.epoch(
                task_family="coding",
                skill="# Skill",
                proposals=(SkillEdit("add", content="Use evidence."),),
                train_ids=("t1",),
                holdout_ids=("v1",),
                evidence_ids=(),
                score=lambda skill, ids: SkillScore(1.0),
            )

    def test_slow_update_is_bounded(self):
        current = "# Skill\nRule A"
        accepted = "# Skill\nRule A\nRule B\nRule C"
        self.assertEqual(self.optimizer.slow_update(current, accepted, rate=0.5), "# Skill\nRule A\nRule B\n")


if __name__ == "__main__":
    unittest.main()
