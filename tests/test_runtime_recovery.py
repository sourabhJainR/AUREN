from __future__ import annotations
import unittest

from portable.orchestration import Graph, Node, NodeKind, RunStatus
from portable.state_graph import GraphAgentTeam, StateGraph
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

    def test_specialist_state_graph_reuses_supervisor_guard_instance(self):
        guard = RuntimeExecutionGuard(max_steps=4, max_attempts=4)
        seen = []
        graph = StateGraph(Graph([
            Node(
                "work",
                NodeKind.DETERMINISTIC,
                lambda state: seen.append(state["_auren_runtime_execution_guard"]) or "ok",
            )
        ]))
        team = GraphAgentTeam(graph)

        result = team.run(
            "shared-guard-task",
            "execute specialist graph",
            {"_auren_runtime_execution_guard": guard},
        )

        self.assertEqual(RunStatus.ACCEPTED, result.status)
        self.assertEqual(1, len(seen))
        self.assertIs(guard, seen[0])
        self.assertEqual(1, guard.attempts_used)

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



    def test_absolute_deadline_expires_during_downtime(self):
        import time
        from portable.runtime_recovery import _digest
        guard = RuntimeExecutionGuard(deadline_seconds=30)
        snapshot = guard.snapshot()
        snapshot["deadline_unix"] = time.time() - 1
        payload = {key: value for key, value in snapshot.items() if key != "digest"}
        snapshot["digest"] = _digest(payload)
        resumed = RuntimeExecutionGuard.resume(snapshot)
        self.assertTrue(resumed.cancellation_event.is_set())
        with self.assertRaises(ExecutionBudgetExceeded):
            resumed.check()

    def test_deadline_checkpoint_without_absolute_expiry_fails_closed(self):
        from portable.runtime_recovery import _digest
        guard = RuntimeExecutionGuard(deadline_seconds=30)
        snapshot = guard.snapshot()
        snapshot.pop("deadline_unix")
        payload = {key: value for key, value in snapshot.items() if key != "digest"}
        snapshot["digest"] = _digest(payload)
        with self.assertRaisesRegex(ValueError, "lacks absolute deadline"):
            RuntimeExecutionGuard.resume(snapshot)

    def test_tighten_deadline_never_extends_shared_budget(self):
        guard = RuntimeExecutionGuard(max_steps=4, max_attempts=4)
        first_remaining = guard.tighten_deadline(0.5)
        self.assertLessEqual(first_remaining, 0.5)
        shortened_remaining = guard.snapshot()["deadline_remaining_seconds"]
        guard.tighten_deadline(10)
        self.assertLessEqual(guard.snapshot()["deadline_remaining_seconds"], shortened_remaining)
        with self.assertRaises(ValueError):
            guard.tighten_deadline(float("nan"))

    def test_non_finite_or_boolean_deadlines_are_rejected(self):
        for value in (float("nan"), float("inf"), True):
            with self.assertRaisesRegex(ValueError, "finite positive"):
                RuntimeExecutionGuard(deadline_seconds=value)

if __name__ == "__main__":
    unittest.main()
