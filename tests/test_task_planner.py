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


if __name__ == "__main__":
    unittest.main()
