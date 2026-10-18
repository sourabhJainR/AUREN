from __future__ import annotations

import unittest

from portable.engineering_plan import (
    EngineeringPlan, EngineeringPlanRun, PlanTask, TaskProgress, new_plan_run,
)


def sample_plan() -> EngineeringPlan:
    return EngineeringPlan(
        run_id="run-1",
        goal="Ship a verified feature",
        requirements=("R1: feature behaves correctly", "R2: rollback is possible"),
        source_revision="abc123",
        budget={"attempts": 8, "steps": 20},
        tasks=(
            PlanTask(
                task_id="design", title="Design the change",
                acceptance_criteria=("design decision is recorded",),
                rollback_plan="revert the design commit",
            ),
            PlanTask(
                task_id="implement", title="Implement the change",
                acceptance_criteria=("unit tests pass", "security checks pass"),
                rollback_plan="revert implementation and restore prior behavior",
                depends_on=("design",), risk="high",
            ),
        ),
    )


class EngineeringPlanTests(unittest.TestCase):
    def test_plan_digest_is_deterministic_and_tracks_requirements(self):
        plan = sample_plan()
        self.assertEqual(plan.digest, sample_plan().digest)
        changed = EngineeringPlan(
            run_id=plan.run_id, goal=plan.goal,
            requirements=("R1 changed",), tasks=plan.tasks,
            source_revision=plan.source_revision, budget=plan.budget,
        )
        self.assertNotEqual(plan.digest, changed.digest)

    def test_rejects_unknown_schema_and_empty_acceptance_criteria(self):
        with self.assertRaisesRegex(ValueError, "unsupported plan schema"):
            EngineeringPlan(
                run_id="run", goal="goal", requirements=("req",),
                tasks=(PlanTask("t", "title", ("done",), "rollback"),),
                schema_version="future-schema",
            )
        with self.assertRaisesRegex(ValueError, "acceptance criteria"):
            PlanTask("t", "title", (), "rollback")

    def test_rejects_missing_dependencies_and_cycles(self):
        with self.assertRaisesRegex(ValueError, "unknown dependencies"):
            EngineeringPlan(
                run_id="run", goal="goal", requirements=("req",),
                tasks=(PlanTask("a", "A", ("done",), "undo", depends_on=("missing",)),),
            )
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            EngineeringPlan(
                run_id="run", goal="goal", requirements=("req",),
                tasks=(
                    PlanTask("a", "A", ("done",), "undo", depends_on=("b",)),
                    PlanTask("b", "B", ("done",), "undo", depends_on=("a",)),
                ),
            )

    def test_dependency_scheduler_returns_only_unblocked_tasks(self):
        plan = sample_plan()
        self.assertEqual(("design",), plan.ready_tasks(set()))
        self.assertEqual(("implement",), plan.ready_tasks({"design"}))
        self.assertEqual((), plan.ready_tasks({"design", "implement"}))
        with self.assertRaisesRegex(ValueError, "outside this plan"):
            plan.ready_tasks({"unknown"})

    def test_pass_requires_evidence_and_terminal_state_cannot_reopen(self):
        progress = TaskProgress("design").transition("ready", reason="dependencies satisfied")
        progress = progress.transition("running", reason="executor started")
        progress = progress.transition("verifying", reason="implementation complete")
        with self.assertRaisesRegex(ValueError, "without evidence"):
            progress.transition("passed", reason="looks good")
        passed = progress.transition("passed", reason="verified", evidence_refs=("ci://run/1",))
        with self.assertRaisesRegex(ValueError, "illegal task transition"):
            passed.transition("ready", reason="try again", replan_generation=1)

    def test_failed_task_replan_must_advance_generation(self):
        failed = TaskProgress("task").transition("ready", reason="ready")
        failed = failed.transition("running", reason="started")
        failed = failed.transition("failed", reason="tests failed")
        with self.assertRaisesRegex(ValueError, "advance replan_generation"):
            failed.transition("ready", reason="replan without increment", replan_generation=0)
        reopened = failed.transition(
            "ready", reason="targeted plan revised", replan_generation=1,
        )
        self.assertEqual(1, reopened.replan_generation)
        self.assertEqual("ready", reopened.status)

    def test_run_resume_rejects_changed_plan_digest_or_revision(self):
        plan = sample_plan()
        run = new_plan_run(plan)
        run.verify_against(plan)
        changed = EngineeringPlan(
            run_id=plan.run_id, goal=plan.goal, requirements=("changed",),
            tasks=plan.tasks, source_revision=plan.source_revision, budget=plan.budget,
        )
        with self.assertRaisesRegex(ValueError, "plan digest mismatch"):
            run.verify_against(changed)
        different_revision = EngineeringPlan(
            run_id=plan.run_id, goal=plan.goal, requirements=plan.requirements,
            tasks=plan.tasks, source_revision="different-revision", budget=plan.budget,
        )
        with self.assertRaisesRegex(ValueError, "plan digest mismatch"):
            run.verify_against(different_revision)

    def test_transition_is_immutable_and_advances_run_revision(self):
        plan = sample_plan()
        run = new_plan_run(plan)
        advanced = run.transition_task("design", "ready", reason="no dependencies")
        self.assertEqual(0, run.revision)
        self.assertEqual(1, advanced.revision)
        self.assertEqual("pending", run.tasks[0].status)
        self.assertEqual("ready", advanced.tasks[0].status)
        with self.assertRaisesRegex(KeyError, "not present"):
            run.transition_task("unknown", "ready", reason="invalid")

    def test_run_rejects_duplicate_task_ids_and_invalid_digest(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            EngineeringPlanRun(
                plan_digest="a" * 64, source_revision="rev",
                tasks=(TaskProgress("x"), TaskProgress("x")),
            )
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            EngineeringPlanRun(
                plan_digest="not-a-digest", source_revision="rev",
                tasks=(TaskProgress("x"),),
            )


if __name__ == "__main__":
    unittest.main()
