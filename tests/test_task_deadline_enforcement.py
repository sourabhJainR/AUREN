from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from portable.adaptive_runtime import AdaptiveRuntime
from portable.orchestration import Graph, Node, NodeKind, NodeStatus, OrchestrationRun, Orchestrator


def iso_offset(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class TaskDeadlineEnforcementTests(unittest.TestCase):
    def test_expired_deadline_blocks_graph_node_before_side_effect(self):
        calls = []
        node = Node("write", NodeKind.DETERMINISTIC, lambda _state: calls.append("ran"))
        orchestrator = Orchestrator(Graph([node]))
        run = OrchestrationRun(task_id="task", intent_digest="intent")
        result = orchestrator._execute_node(
            node,
            {"auren_deadline_at": iso_offset(-1)},
            run,
            total_attempts=0,
        )
        self.assertEqual(result.status, NodeStatus.FAILED)
        self.assertIn("task deadline exceeded", result.error)
        self.assertEqual(calls, [])
        self.assertEqual(result.attempts, 0)

    def test_graph_node_receives_refreshed_remaining_task_budget(self):
        observed = []
        node = Node(
            "bounded-work", NodeKind.DETERMINISTIC,
            lambda state: observed.append(state.get("auren_remaining_seconds")) or "ok",
        )
        orchestrator = Orchestrator(Graph([node]))
        run = OrchestrationRun(task_id="task", intent_digest="intent")
        result = orchestrator._execute_node(
            node, {"auren_deadline_at": iso_offset(30)}, run, total_attempts=0,
        )
        self.assertEqual(result.status, NodeStatus.PASSED)
        self.assertEqual(len(observed), 1)
        self.assertGreater(observed[0], 0)
        self.assertLessEqual(observed[0], 30)

    def test_invalid_or_timezone_naive_deadline_fails_closed(self):
        node = Node("work", NodeKind.DETERMINISTIC, lambda _state: "should not run")
        orchestrator = Orchestrator(Graph([node]))
        run = OrchestrationRun(task_id="task", intent_digest="intent")
        for value in ("not-a-date", "2030-01-01T00:00:00"):
            with self.subTest(value=value):
                result = orchestrator._execute_node(
                    node, {"auren_deadline_at": value}, run, total_attempts=0,
                )
                self.assertEqual(result.status, NodeStatus.FAILED)
                self.assertIn("invalid task deadline", result.error)

    def test_default_runtime_preserves_expired_deadline_and_never_starts_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda _state: "ok")])
            runtime = AdaptiveRuntime(graph, state_root=root / "state")
            calls = []

            def run_once(**kwargs):
                calls.append(kwargs)
                raise AssertionError("expired task must not enter graph execution")

            runtime._run_once = run_once
            expired_deadline = iso_offset(-1)
            with self.assertRaisesRegex(RuntimeError, "task deadline exceeded"):
                runtime.run(
                    session_id="deadline-session",
                    task_id="deadline-task",
                    project_root=root,
                    intent="enforce an expired deadline",
                    context={"auren_deadline_at": expired_deadline, "auren_max_iterations": 2},
                )
            self.assertEqual(calls, [])
            with sqlite3.connect(runtime.execution_controller.db_path) as db:
                row = db.execute(
                    "SELECT work_unit_id FROM sessions WHERE session_id=?",
                    ("deadline-session:deadline-task",),
                ).fetchone()
            self.assertIsNotNone(row)
            checkpoint = runtime.recovery_ledger.get_checkpoint(row[0], "execution")
            self.assertIsNotNone(checkpoint)
            self.assertEqual(checkpoint.status, "blocked")
            self.assertEqual(checkpoint.payload["deadline_at"], expired_deadline)

    def test_deadline_seconds_must_be_bounded_integer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda _state: "ok")])
            for invalid in (0, -1, 3601, True, "30"):
                with self.subTest(invalid=invalid):
                    runtime = AdaptiveRuntime(graph, state_root=root / f"state-{invalid!r}")
                    with self.assertRaisesRegex(ValueError, "auren_deadline_seconds"):
                        runtime.run(
                            session_id="bounded-deadline-session",
                            task_id="bounded-deadline-task",
                            project_root=root,
                            intent="validate deadline bounds",
                            context={"auren_deadline_seconds": invalid},
                        )


    def test_deadline_expiring_during_graph_execution_cannot_be_reported_accepted(self):
        import time

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def overrun(_state):
                time.sleep(0.15)
                return "completed too late"

            graph = Graph([Node("slow-work", NodeKind.DETERMINISTIC, overrun)])
            runtime = AdaptiveRuntime(graph, state_root=root / "state")
            with self.assertRaisesRegex(RuntimeError, "task deadline exceeded"):
                runtime.run(
                    session_id="overrun-session",
                    task_id="overrun-task",
                    project_root=root,
                    intent="do not accept work after the deadline",
                    context={"auren_deadline_at": iso_offset(0.05)},
                )

            work_id = runtime.execution_controller.work_unit_for_session(
                "overrun-session:overrun-task", "overrun-task: do not accept work after the deadline"
            )
            checkpoint = runtime.recovery_ledger.get_checkpoint(work_id, "execution")
            self.assertIsNotNone(checkpoint)
            self.assertEqual(checkpoint.status, "blocked")
            self.assertEqual(checkpoint.payload["outcome"], "blocked")
            self.assertIn("deadline", checkpoint.payload["last_failure"])

if __name__ == "__main__":
    unittest.main()
