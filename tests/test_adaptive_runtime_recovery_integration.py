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


if __name__ == "__main__":
    unittest.main()
