from __future__ import annotations

import unittest

from portable.crew_adapters import TaskRetryBudget, merge_retry_budget_snapshots


class TaskRetryBudgetTests(unittest.TestCase):
    def test_budget_is_bounded_and_idempotent(self) -> None:
        budget = TaskRetryBudget(max_retries=2)
        self.assertTrue(budget.consume(component="agent", operation_key="agent-a:retry-1"))
        self.assertFalse(budget.consume(component="agent", operation_key="agent-a:retry-1"))
        self.assertEqual(budget.used, 1)
        self.assertTrue(budget.consume(component="crew", operation_key="crew-ui:retry-1"))
        self.assertFalse(budget.consume(component="supervisor", operation_key="supervisor:retry-1"))
        self.assertEqual(budget.remaining, 0)
        with self.assertRaises(ValueError):
            budget.consume(component="different-component", operation_key="agent-a:retry-1")

    def test_checkpoint_round_trip_validates_digest_and_budget(self) -> None:
        budget = TaskRetryBudget(max_retries=3)
        budget.consume(component="agent", operation_key="agent-a:retry-1")
        saved = budget.snapshot()
        restored = TaskRetryBudget.resume(saved)
        self.assertEqual(restored.used, 1)
        self.assertEqual(restored.remaining, 2)
        corrupt = dict(saved)
        corrupt["used_retries"] = 2
        with self.assertRaises(ValueError):
            TaskRetryBudget.resume(corrupt)
        corrupt = dict(saved)
        corrupt["max_retries"] = 4
        with self.assertRaises(ValueError):
            TaskRetryBudget.resume(corrupt)

    def test_concurrent_branch_snapshots_merge_monotonically(self) -> None:
        left = TaskRetryBudget(max_retries=3)
        right = TaskRetryBudget(max_retries=3)
        left.consume(component="agent", operation_key="agent-a:retry-1")
        right.consume(component="agent", operation_key="agent-b:retry-1")
        merged = merge_retry_budget_snapshots(left.snapshot(), right.snapshot())
        restored = TaskRetryBudget.resume(merged)
        self.assertEqual(restored.used, 2)
        self.assertEqual(restored.remaining, 1)
        repeated = merge_retry_budget_snapshots(merged, left.snapshot())
        self.assertEqual(TaskRetryBudget.resume(repeated).used, 2)

    def test_merge_rejects_conflicting_or_over_budget_snapshots(self) -> None:
        left = TaskRetryBudget(max_retries=1)
        right = TaskRetryBudget(max_retries=1)
        left.consume(component="agent", operation_key="same")
        right.consume(component="crew", operation_key="same")
        with self.assertRaises(ValueError):
            merge_retry_budget_snapshots(left.snapshot(), right.snapshot())
        left = TaskRetryBudget(max_retries=1)
        right = TaskRetryBudget(max_retries=1)
        left.consume(component="agent", operation_key="a")
        right.consume(component="crew", operation_key="b")
        with self.assertRaises(ValueError):
            merge_retry_budget_snapshots(left.snapshot(), right.snapshot())


if __name__ == "__main__":
    unittest.main()
