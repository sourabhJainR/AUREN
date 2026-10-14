from __future__ import annotations

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
            work_id = runtime.execution_controller.work_unit_for_session(
                "deadline-session:deadline-task", "deadline-task: enforce an expired deadline"
            )
            checkpoint = runtime.recovery_ledger.get_checkpoint(work_id, "execution")
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


if __name__ == "__main__":
    unittest.main()
