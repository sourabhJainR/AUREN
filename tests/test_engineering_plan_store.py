from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable.engineering_plan import EngineeringPlan, PlanTask, new_plan_run
from portable.engineering_plan_store import EngineeringPlanStore, PlanRevisionConflict


def make_plan(run_id: str = "store-run") -> EngineeringPlan:
    return EngineeringPlan(
        run_id=run_id, goal="Persist plan progress",
        requirements=("state survives restart",),
        source_revision="revision-123",
        tasks=(
            PlanTask("spec", "Write specification", ("spec reviewed",), "revert spec"),
            PlanTask(
                "test", "Verify implementation", ("tests pass",), "revert implementation",
                depends_on=("spec",),
            ),
        ),
    )


class EngineeringPlanStoreTests(unittest.TestCase):
    def test_create_load_and_resume_preserve_digests(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EngineeringPlanStore(Path(tmp) / "plans.db")
            plan = make_plan()
            original = store.create(plan)
            loaded_plan, loaded_run = store.load(plan.run_id)
            self.assertEqual(plan.digest, loaded_plan.digest)
            self.assertEqual(original.digest, loaded_run.digest)
            loaded_run.verify_against(loaded_plan)

    def test_transition_is_atomically_saved_and_survives_store_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "plans.db"
            plan = make_plan()
            store = EngineeringPlanStore(db_path)
            run = store.create(plan)
            next_run = run.transition_task("spec", "ready", plan=plan, reason="no dependencies")
            store.save_run(plan.run_id, next_run, expected_revision=0)
            reopened = EngineeringPlanStore(db_path)
            loaded_plan, loaded_run = reopened.load(plan.run_id)
            self.assertEqual(1, loaded_run.revision)
            self.assertEqual("ready", loaded_run.tasks[0].status)
            self.assertEqual(plan.digest, loaded_plan.digest)

    def test_compare_and_swap_rejects_stale_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EngineeringPlanStore(Path(tmp) / "plans.db")
            plan = make_plan()
            first = store.create(plan)
            writer_a = first.transition_task("spec", "ready", plan=plan, reason="ready")
            writer_b = first.transition_task("spec", "ready", plan=plan, reason="other writer")
            store.save_run(plan.run_id, writer_a, expected_revision=0)
            with self.assertRaises(PlanRevisionConflict):
                store.save_run(plan.run_id, writer_b, expected_revision=0)
            _, current = store.load(plan.run_id)
            self.assertEqual(writer_a.digest, current.digest)

    def test_create_rejects_duplicate_run_and_save_rejects_skipped_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EngineeringPlanStore(Path(tmp) / "plans.db")
            plan = make_plan()
            run = store.create(plan)
            with self.assertRaisesRegex(ValueError, "already exists"):
                store.create(plan)
            with self.assertRaisesRegex(ValueError, "advance exactly one revision"):
                store.save_run(
                    plan.run_id,
                    type(run)(
                        plan_digest=run.plan_digest, source_revision=run.source_revision,
                        tasks=run.tasks, revision=2,
                    ),
                    expected_revision=0,
                )

    def test_load_fails_closed_on_corrupt_or_tampered_persisted_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "plans.db"
            store = EngineeringPlanStore(db_path)
            plan = make_plan()
            store.create(plan)
            db = sqlite3.connect(db_path)
            try:
                with db:
                    db.execute(
                        "UPDATE engineering_plan_runs SET plan_json = ? WHERE run_id = ?",
                        ('{"schema_version":"unknown"}', plan.run_id),
                    )
            finally:
                db.close()
            with self.assertRaisesRegex(ValueError, "unsupported plan schema"):
                store.load(plan.run_id)

    def test_unknown_run_is_not_treated_as_empty_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EngineeringPlanStore(Path(tmp) / "plans.db")
            with self.assertRaisesRegex(KeyError, "does not exist"):
                store.load("missing-run")


if __name__ == "__main__":
    unittest.main()
