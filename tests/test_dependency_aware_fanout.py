from __future__ import annotations

import threading
import time
import unittest

from portable.dependency_aware_fanout import DependencyAwareFanout
from portable.runtime_recovery import ExecutionBudgetExceeded, RuntimeExecutionGuard
from portable.task_planner import Task, TaskPlan


def make_fanout(plan, execute, **kwargs):
    return DependencyAwareFanout(
        plan, execute,
        expected_plan_digest=DependencyAwareFanout.plan_digest(plan),
        expected_intent_digest="intent-v1", current_intent_digest="intent-v1",
        **kwargs,
    )


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
        result = make_fanout(
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
        result = make_fanout(
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
        result = make_fanout(
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
        result = make_fanout(
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
        result = make_fanout(
            plan, lambda task: object(),
            execution_guard=RuntimeExecutionGuard(max_steps=1, max_attempts=1),
            max_workers=1,
        ).run()
        self.assertEqual(result.statuses["a"], "failed")
        self.assertIn("TypeError", result.errors["a"])
        self.assertNotEqual(plan.tasks["a"].status, "done")

    def test_stale_plan_digest_fails_before_dispatch(self):
        plan = TaskPlan([Task("a", "A", metadata={"execution_mode": "read_only"})])
        called = []
        runner = DependencyAwareFanout(
            plan, lambda task: called.append(task.id),
            execution_guard=RuntimeExecutionGuard(max_steps=1, max_attempts=1),
            expected_plan_digest="stale-plan", expected_intent_digest="intent-v1",
            current_intent_digest="intent-v1",
        )
        with self.assertRaisesRegex(ValueError, "stale plan digest"):
            runner.run()
        self.assertEqual(called, [])
        self.assertEqual(runner.guard.snapshot()["steps_used"], 0)

    def test_stale_intent_digest_fails_before_dispatch(self):
        plan = TaskPlan([Task("a", "A", metadata={"execution_mode": "read_only"})])
        called = []
        runner = DependencyAwareFanout(
            plan, lambda task: called.append(task.id),
            execution_guard=RuntimeExecutionGuard(max_steps=1, max_attempts=1),
            expected_plan_digest=DependencyAwareFanout.plan_digest(plan),
            expected_intent_digest="old-intent", current_intent_digest="new-intent",
        )
        with self.assertRaisesRegex(ValueError, "stale intent digest"):
            runner.run()
        self.assertEqual(called, [])
        self.assertEqual(runner.guard.snapshot()["steps_used"], 0)

    def test_parallelism_skips_overhead_when_expected_gain_is_too_small(self):
        active = 0
        maximum = 0
        lock = threading.Lock()
        def execute(task):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.01)
            with lock:
                active -= 1
        plan = TaskPlan([
            Task("a", "A", metadata={"execution_mode": "read_only", "estimated_duration_ms": 3}),
            Task("b", "B", metadata={"execution_mode": "read_only", "estimated_duration_ms": 3}),
        ])
        result = make_fanout(
            plan, execute, execution_guard=RuntimeExecutionGuard(max_steps=2, max_attempts=2),
            max_workers=2, fanout_overhead_ms=5,
        ).run()
        self.assertTrue(result.succeeded, result.errors)
        self.assertEqual(maximum, 1)

    def test_canonical_evidence_and_memory_writes_are_serial(self):
        active = 0
        maximum = 0
        lock = threading.Lock()
        def execute(task):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.01)
            with lock:
                active -= 1
        plan = TaskPlan([
            Task("evidence", "Evidence", tags=["evidence"], metadata={"execution_mode": "read_only"}),
            Task("memory", "Memory", metadata={"execution_mode": "read_only", "writes_canonical_memory": True}),
        ])
        result = make_fanout(
            plan, execute, execution_guard=RuntimeExecutionGuard(max_steps=2, max_attempts=2),
            max_workers=2,
        ).run()
        self.assertTrue(result.succeeded, result.errors)
        self.assertEqual(maximum, 1)

    def test_dependency_failure_blocks_downstream_without_dispatch(self):
        called = []
        plan = TaskPlan([
            Task("a", "A", metadata={"execution_mode": "read_only"}),
            Task("b", "B", dependencies=["a"], metadata={"execution_mode": "read_only"}),
        ])

        def execute(task):
            called.append(task.id)
            raise RuntimeError("failure") if task.id == "a" else AssertionError("must not run")

        result = make_fanout(
            plan, execute, execution_guard=RuntimeExecutionGuard(max_steps=2, max_attempts=2),
            max_workers=2,
        ).run()
        self.assertEqual(called, ["a"])
        self.assertEqual(result.statuses["a"], "failed")
        self.assertEqual(result.statuses["b"], "blocked")


if __name__ == "__main__":
    unittest.main()
