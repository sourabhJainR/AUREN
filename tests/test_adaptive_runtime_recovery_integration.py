from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable.adaptive_runtime import AdaptiveRuntime
from portable.orchestration import Graph, Node, NodeKind


class AdaptiveRuntimeRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_default_runtime_uses_bounded_recovery_ledger_and_alternate_plan(self):
        graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda state: "ok")])
        runtime = AdaptiveRuntime(graph, state_root=self.root / "state")
        observed_contexts = []

        def failing_execution(**kwargs):
            observed_contexts.append(dict(kwargs.get("context") or {}))
            raise RuntimeError("repeatable test failure")

        runtime._run_once = failing_execution
        with self.assertRaisesRegex(RuntimeError, "stopped safely"):
            runtime.run(
                session_id="recovery-session",
                task_id="recovery-task",
                project_root=self.root,
                intent="exercise durable recovery",
                context={"auren_max_iterations": 3},
            )

        with sqlite3.connect(runtime.execution_controller.db_path) as db:
            work_id = db.execute(
                "SELECT work_unit_id FROM sessions WHERE session_id=?",
                ("recovery-session:recovery-task",),
            ).fetchone()[0]
        attempts = runtime.recovery_ledger.attempts(work_id)
        self.assertEqual(
            [item.hypothesis for item in attempts],
            ["default-plan", "default-plan", "alternate-plan-1"],
        )
        self.assertEqual(
            [item.action for item in attempts],
            ["reproduce", "narrow_fix", "alternate_implementation"],
        )
        self.assertTrue(all(item.outcome == "failed" for item in attempts))
        self.assertEqual(
            [item["auren_recovery_mode"] for item in observed_contexts],
            ["initial", "fresh-retry", "alternate-plan"],
        )
        checkpoint = runtime.recovery_ledger.get_checkpoint(work_id, "execution")
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint.status, "blocked")
        self.assertEqual(checkpoint.payload["hypothesis"], "alternate-plan-1")

        # Recovery state is durable across a fresh default-runtime instance.
        resumed = AdaptiveRuntime(graph, state_root=self.root / "state")
        resumed_attempts = resumed.recovery_ledger.attempts(work_id)
        self.assertEqual([item.hypothesis for item in resumed_attempts], [item.hypothesis for item in attempts])
        self.assertEqual(resumed.recovery_ledger.get_checkpoint(work_id, "execution").payload["recovery_mode"], "alternate-plan")

    def test_supervisor_shares_and_checkpoints_one_runtime_guard_across_retries(self):
        graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda state: "ok")])
        runtime = AdaptiveRuntime(graph, state_root=self.root / "shared-guard-state")
        seen_guards = []

        def failing_execution(**kwargs):
            context = dict(kwargs.get("context") or {})
            guard = context.get("_auren_runtime_execution_guard")
            self.assertIsNotNone(guard)
            seen_guards.append(guard)
            guard.charge_attempt(f"provider-call:{len(seen_guards)}")
            raise RuntimeError("exercise shared budget checkpoint")

        runtime._run_once = failing_execution
        with self.assertRaisesRegex(RuntimeError, "stopped safely"):
            runtime.run(
                session_id="shared-guard-session",
                task_id="shared-guard-task",
                project_root=self.root,
                intent="prove shared runtime budget across supervisor retries",
                context={"auren_max_iterations": 3},
            )

        self.assertEqual(3, len(seen_guards))
        self.assertTrue(all(guard is seen_guards[0] for guard in seen_guards))
        self.assertEqual(6, seen_guards[0].attempts_used)  # 3 supervisor + 3 provider attempts

        with sqlite3.connect(runtime.execution_controller.db_path) as db:
            work_id = db.execute(
                "SELECT work_unit_id FROM sessions WHERE session_id=?",
                ("shared-guard-session:shared-guard-task",),
            ).fetchone()[0]
        checkpoint = runtime.recovery_ledger.get_checkpoint(work_id, "execution")
        self.assertIsNotNone(checkpoint)
        restored = __import__("portable.runtime_recovery", fromlist=["RuntimeExecutionGuard"]).RuntimeExecutionGuard.resume(
            checkpoint.payload["runtime_guard"]
        )
        self.assertEqual(6, restored.attempts_used)
        self.assertEqual(
            checkpoint.payload["runtime_guard"]["digest"],
            restored.snapshot()["digest"],
        )

    def test_default_runtime_migrates_legacy_sessions_on_startup(self):
        legacy = self.root / ".aer"
        canonical = self.root / ".auren"
        old_session = legacy / "sessions" / "legacy-session.json"
        old_session.parent.mkdir(parents=True)
        old_session.write_text('{"session_id":"legacy-session","stage":"resume"}', encoding="utf-8")

        graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda state: "ok")])
        runtime = AdaptiveRuntime(graph, state_root=canonical, legacy_state_root=legacy)

        migrated = canonical / "sessions" / "legacy-session.json"
        self.assertTrue(migrated.is_file())
        self.assertTrue(old_session.is_file(), "runtime migration must retain the legacy source")
        self.assertEqual(runtime.session_store.root, (canonical / "sessions").resolve())
        self.assertEqual(migrated.read_text(encoding="utf-8"), old_session.read_text(encoding="utf-8"))

    def test_default_runtime_rejects_unbounded_iteration_requests(self):
        graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda state: "ok")])
        runtime = AdaptiveRuntime(graph, state_root=self.root / "state")
        with self.assertRaisesRegex(ValueError, "between 1 and 12"):
            runtime.run(
                session_id="bounded-session",
                task_id="bounded-task",
                project_root=self.root,
                intent="bound iteration count",
                context={"auren_max_iterations": 1000000},
            )

    def test_default_runtime_continues_independent_work_when_dependency_is_blocked(self):
        calls = []

        def fail_critical(_state):
            calls.append("critical")
            raise RuntimeError("required dependency failed")

        def dependent(_state):
            calls.append("dependent")
            return "must not run"

        def independent(_state):
            calls.append("independent")
            return "safe independent work completed"

        graph = Graph([
            Node("a-critical", NodeKind.DETERMINISTIC, fail_critical, critical=True),
            Node("b-dependent", NodeKind.DETERMINISTIC, dependent, depends_on=("a-critical",), critical=False),
            Node("z-independent", NodeKind.DETERMINISTIC, independent, critical=False),
        ])
        runtime = AdaptiveRuntime(graph, state_root=self.root / "state")
        with self.assertRaisesRegex(RuntimeError, "stopped safely"):
            runtime.run(
                session_id="blocked-dependency-session",
                task_id="blocked-dependency-task",
                project_root=self.root,
                intent="continue safe independent work around a blocked dependency",
                context={"auren_max_iterations": 1},
            )

        self.assertEqual(calls, ["critical", "independent"])
        with sqlite3.connect(runtime.execution_controller.db_path) as db:
            work_id = db.execute(
                "SELECT work_unit_id FROM sessions WHERE session_id=?",
                ("blocked-dependency-session:blocked-dependency-task",),
            ).fetchone()[0]
        attempts = runtime.recovery_ledger.attempts(work_id)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].outcome, "failed")
        checkpoint = runtime.recovery_ledger.get_checkpoint(work_id, "execution")
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint.status, "blocked")
        self.assertIn("required dependency failed", checkpoint.payload["last_failure"])

        # A fresh runtime sees the same blocked terminal checkpoint; it must not
        # silently convert a failed critical dependency into an accepted run.
        resumed = AdaptiveRuntime(graph, state_root=self.root / "state")
        resumed_checkpoint = resumed.recovery_ledger.get_checkpoint(work_id, "execution")
        self.assertIsNotNone(resumed_checkpoint)
        self.assertEqual(resumed_checkpoint.status, "blocked")
        self.assertEqual(resumed_checkpoint.payload["outcome"], "blocked")


if __name__ == "__main__":
    unittest.main()
