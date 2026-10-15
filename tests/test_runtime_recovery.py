from __future__ import annotations
import unittest

from portable.runtime_recovery import (
    ExecutionBudgetExceeded, NoProgressDetected, RuntimeExecutionGuard,
)


class RuntimeExecutionGuardTests(unittest.TestCase):
    def test_shared_attempt_budget_and_idempotent_charge(self):
        guard = RuntimeExecutionGuard(max_attempts=2)
        self.assertTrue(guard.charge_attempt("node:a:1"))
        self.assertFalse(guard.charge_attempt("node:a:1"))
        self.assertTrue(guard.charge_attempt("handoff:b:1"))
        with self.assertRaises(ExecutionBudgetExceeded):
            guard.charge_attempt("supervisor:retry:1")

    def test_step_budget_is_bounded(self):
        guard = RuntimeExecutionGuard(max_steps=1)
        self.assertEqual(guard.charge_step(), 1)
        with self.assertRaises(ExecutionBudgetExceeded):
            guard.charge_step()

    def test_identical_failure_stops_retry_cycle(self):
        guard = RuntimeExecutionGuard(repeated_failure_limit=2)
        self.assertEqual(guard.observe_failure(node="build", state={"x": 1}, error=ValueError("same")), 1)
        with self.assertRaises(NoProgressDetected):
            guard.observe_failure(node="build", state={"x": 1}, error=ValueError("same"))
        self.assertTrue(guard.cancellation_event.is_set())

    def test_progress_clears_previous_node_failure_streak(self):
        guard = RuntimeExecutionGuard(repeated_failure_limit=2)
        guard.observe_failure(node="build", state={"x": 1}, error=ValueError("same"))
        guard.observe_progress(node="build", state={"x": 2})
        self.assertEqual(guard.observe_failure(node="build", state={"x": 2}, error=ValueError("same")), 1)

    def test_snapshot_resume_preserves_charges_and_deduplicates(self):
        guard = RuntimeExecutionGuard(max_steps=5, max_attempts=3)
        guard.charge_step()
        guard.charge_attempt("graph:node:1")
        resumed = RuntimeExecutionGuard.resume(guard.snapshot())
        self.assertFalse(resumed.charge_attempt("graph:node:1"))
        self.assertTrue(resumed.charge_attempt("handoff:task:1"))
        self.assertEqual(resumed.snapshot()["steps_used"], 1)
        self.assertEqual(resumed.snapshot()["attempts_used"], 2)

    def test_tampered_snapshot_fails_closed(self):
        guard = RuntimeExecutionGuard()
        snapshot = guard.snapshot()
        snapshot["attempts_used"] = 99
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            RuntimeExecutionGuard.resume(snapshot)

    def test_cancelled_snapshot_cannot_resume_as_live(self):
        guard = RuntimeExecutionGuard()
        guard.cancel("operator stop")
        resumed = RuntimeExecutionGuard.resume(guard.snapshot())
        with self.assertRaisesRegex(ExecutionBudgetExceeded, "operator stop"):
            resumed.check()


if __name__ == "__main__":
    unittest.main()
