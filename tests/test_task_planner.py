from __future__ import annotations

import unittest

from portable.task_planner import Task, TaskPlan


class TaskPlannerTests(unittest.TestCase):
    def test_ready_respects_dependencies_and_priority(self) -> None:
        plan = TaskPlan([
            Task("2", "Second", dependencies=["1"], priority="high"),
            Task("1", "First", priority="medium"),
            Task("3", "Independent", priority="low", tags=["docs"]),
        ])
        self.assertEqual([task.id for task in plan.ready()], ["1", "3"])
        plan.tasks["1"].status = "done"
        self.assertEqual(plan.next().id, "2")
        self.assertIsNone(plan.next("missing"))

    def test_cycle_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TaskPlan([Task("1", "A", dependencies=["2"]), Task("2", "B", dependencies=["1"])])

    def test_missing_dependency_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TaskPlan([Task("1", "A", dependencies=["99"])])

    def test_digest_ignores_mutable_status_and_execution_output(self) -> None:
        plan = TaskPlan([Task("a", "A", metadata={"read_only": True})])
        original = plan.digest()
        plan.tasks["a"].status = "done"
        plan.tasks["a"].metadata["execution_output"] = {"result": "verified"}
        self.assertEqual(original, plan.digest())

    def test_digest_changes_when_dependency_or_execution_scope_changes(self) -> None:
        plan = TaskPlan([
            Task("a", "A", metadata={"read_only": True}),
            Task("b", "B", metadata={"read_only": False}),
        ])
        original = plan.digest()
        plan.tasks["b"].dependencies = ["a"]
        self.assertNotEqual(original, plan.digest())
        plan.tasks["b"].dependencies = []
        plan.tasks["a"].metadata["read_only"] = False
        self.assertNotEqual(original, plan.digest())

    def test_digest_rejects_non_json_task_definitions(self) -> None:
        plan = TaskPlan([Task("a", "A", metadata={"unsafe": object()})])
        with self.assertRaisesRegex(ValueError, "finite JSON-compatible"):
            plan.digest()

    def _readiness_assessment(self, task_id: str):
        import tempfile
        from pathlib import Path
        from portable.engineering_readiness import EngineeringReadinessGate
        from portable.progress_journal import ProgressJournal

        axes = {
            "technology": {"status": "ready", "summary": "Runtime verified.", "evidence_refs": ["runtime:verified"]},
            "intent": {"status": "ready", "summary": "Acceptance is explicit.", "evidence_refs": ["plan:acceptance"]},
            "cognitive": {"status": "ready", "summary": "Context is complete.", "evidence_refs": ["context:verified"]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            gate = EngineeringReadinessGate(ProgressJournal(Path(tmp) / "progress.jsonl"))
            return gate.assess(
                task_id=task_id, work_key="work:" + task_id,
                intent_digest="a" * 64, repository_snapshot_digest="b" * 64,
                axes=axes, context_plan_digest="c" * 64,
            )

    def test_engineering_task_waits_for_evidence_bound_readiness_receipt(self) -> None:
        task = Task("implement", "Implement feature", tags=["engineering"])
        plan = TaskPlan([task])
        self.assertEqual([], plan.ready())
        plan.bind_engineering_readiness("implement", self._readiness_assessment("implement"))
        self.assertEqual(["implement"], [item.id for item in plan.ready()])
        task.metadata["engineering_readiness"]["assessment_digest"] = "forged"
        self.assertEqual([], plan.ready())

    def test_engineering_task_rejects_receipt_for_another_task(self) -> None:
        plan = TaskPlan([Task("implement", "Implement feature", tags=["engineering"])])
        with self.assertRaisesRegex(ValueError, "different task"):
            plan.bind_engineering_readiness("implement", self._readiness_assessment("different-task"))

    def test_engineering_task_rejects_forged_receipt_object(self) -> None:
        from types import SimpleNamespace

        plan = TaskPlan([Task("implement", "Implement feature", tags=["engineering"])])
        with self.assertRaisesRegex(TypeError, "supervisor-generated"):
            plan.bind_engineering_readiness("implement", SimpleNamespace(to_dict=lambda: {}))


    def test_task_plan_retains_department_owner_and_remediation(self) -> None:
        task = Task(
            "implement", "Implement feature", tags=["engineering"],
            department="Platform Engineering", owner_role_id="ENG-02",
            remediation_steps=["Add missing deployment evidence", "Re-run acceptance"],
        )
        plan = TaskPlan([task])
        self.assertEqual("Platform Engineering", plan.tasks["implement"].department)
        self.assertEqual("ENG-02", plan.tasks["implement"].owner_role_id)
        self.assertEqual(["Add missing deployment evidence", "Re-run acceptance"], plan.tasks["implement"].remediation_steps)



if __name__ == "__main__":
    unittest.main()

# Run durable graph recovery fault tests with canonical TaskPlan regressions.
from tests.test_engineering_recovery import GraphRunRecoveryTests
