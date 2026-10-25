from __future__ import annotations

import threading
import time
import unittest

from portable.dependency_aware_fanout import DependencyAwareFanout
from portable.runtime_recovery import ExecutionBudgetExceeded, RuntimeExecutionGuard
from portable.task_planner import Task, TaskPlan


class DependencyAwareFanoutTests(unittest.TestCase):
    def test_independent_read_only_tasks_overlap_but_dependency_waits(self):
        barrier = threading.Barrier(2)
        events = []
        lock = threading.Lock()

        def execute(task):
            if task.id in {"a", "b"}:
                barrier.wait(timeout=2)
            with lock:
                events.append(task.id)
            return task.id.upper()

        plan = TaskPlan([
            Task("a", "A", metadata={"execution_mode": "read_only"}),
            Task("b", "B", metadata={"execution_mode": "read_only"}),
            Task("c", "C", dependencies=["a", "b"], metadata={"execution_mode": "read_only"}),
        ])
        result = DependencyAwareFanout(
            plan, execute, execution_guard=RuntimeExecutionGuard(max_steps=3, max_attempts=3),
            max_workers=2,
        ).run()
        self.assertTrue(result.succeeded, result.errors)
        self.assertEqual(result.max_observed_concurrency, 2)
        self.assertLess(max(events.index("a"), events.index("b")), events.index("c"))
        self.assertEqual(result.outputs, {"a": "A", "b": "B", "c": "C"})

    def test_mutating_tasks_are_serial_even_when_independent(self):
        active = 0
        max_active = 0
        lock = threading.Lock()

        def execute(task):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1

        plan = TaskPlan([Task("a", "A"), Task("b", "B")])
        result = DependencyAwareFanout(
            plan, execute, execution_guard=RuntimeExecutionGuard(max_steps=2, max_attempts=2),
            max_workers=2,
        ).run()
        self.assertTrue(result.succeeded, result.errors)
        self.assertEqual(max_active, 1)

    def test_shared_step_budget_is_not_reset_between_workers(self):
        started = []
        plan = TaskPlan([
            Task("a", "A", metadata={"execution_mode": "read_only"}),
            Task("b", "B", metadata={"execution_mode": "read_only"}),
        ])
        result = DependencyAwareFanout(
            plan, lambda task: started.append(task.id),
            execution_guard=RuntimeExecutionGuard(max_steps=1, max_attempts=2),
            max_workers=2,
        ).run()
        self.assertIn("failed", result.statuses.values())
        self.assertLessEqual(len(started), 1)
        self.assertTrue(any("budget" in error.lower() for error in result.errors.values()))

    def test_shared_attempt_budget_is_not_reset_between_workers(self):
        started = []
        barrier = threading.Barrier(2)

        def execute(task):
            started.append(task.id)
            return task.id

        plan = TaskPlan([
            Task("a", "A", metadata={"execution_mode": "read_only"}),
            Task("b", "B", metadata={"execution_mode": "read_only"}),
        ])
        result = DependencyAwareFanout(
            plan, execute,
            execution_guard=RuntimeExecutionGuard(max_steps=2, max_attempts=1),
            max_workers=2,
        ).run()
        self.assertIn("failed", result.statuses.values())
        self.assertLessEqual(len(started), 1)
        self.assertTrue(any("attempt budget" in error.lower() for error in result.errors.values()))

    def test_non_checkpointable_output_is_not_reported_as_success(self):
        plan = TaskPlan([
            Task("a", "A", metadata={"execution_mode": "read_only"}),
        ])
        result = DependencyAwareFanout(
            plan, lambda task: object(),
            execution_guard=RuntimeExecutionGuard(max_steps=1, max_attempts=1),
            max_workers=1,
        ).run()
        self.assertEqual(result.statuses["a"], "failed")
        self.assertIn("TypeError", result.errors["a"])
        self.assertNotEqual(plan.tasks["a"].status, "done")

    def test_dependency_failure_blocks_downstream_without_dispatch(self):
        called = []
        plan = TaskPlan([
            Task("a", "A", metadata={"execution_mode": "read_only"}),
            Task("b", "B", dependencies=["a"], metadata={"execution_mode": "read_only"}),
        ])

        def execute(task):
            called.append(task.id)
            raise RuntimeError("failure") if task.id == "a" else AssertionError("must not run")

        result = DependencyAwareFanout(
            plan, execute, execution_guard=RuntimeExecutionGuard(max_steps=2, max_attempts=2),
            max_workers=2,
        ).run()
        self.assertEqual(called, ["a"])
        self.assertEqual(result.statuses["a"], "failed")
        self.assertEqual(result.statuses["b"], "blocked")


if __name__ == "__main__":
    unittest.main()
