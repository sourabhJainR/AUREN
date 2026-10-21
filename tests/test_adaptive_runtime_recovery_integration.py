from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable.adaptive_runtime import AdaptiveRuntime
from portable.orchestration import Graph, Node, NodeKind
from portable.runtime_recovery import RuntimeExecutionGuard


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
        receipts = runtime.recovery_decision_journal.list_for_work(work_id, "execution")
        self.assertEqual([2, 3], [receipt.attempt_number for receipt in receipts])
        self.assertEqual(["retry", "alternate"], [receipt.action for receipt in receipts])
        self.assertEqual("ambiguous", receipts[0].side_effect_state)
        self.assertTrue(all(len(receipt.checkpoint_digest) == 64 for receipt in receipts))

        # Recovery state is durable across a fresh default-runtime instance.
        resumed = AdaptiveRuntime(graph, state_root=self.root / "state")
        resumed_attempts = resumed.recovery_ledger.attempts(work_id)
        self.assertEqual([item.hypothesis for item in resumed_attempts], [item.hypothesis for item in attempts])
        self.assertEqual(resumed.recovery_ledger.get_checkpoint(work_id, "execution").payload["recovery_mode"], "alternate-plan")

    def test_pending_journal_decision_blocks_restart_before_replaying_work(self):
        graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda state: "ok")])
        state_root = self.root / "pending-decision-state"
        runtime = AdaptiveRuntime(graph, state_root=state_root)
        session_id = "pending-decision-session"
        task_id = "pending-decision-task"
        intent = "do not replay an interrupted recovery decision"
        work_id = runtime.execution_controller.work_unit_for_session(
            f"{session_id}:{task_id}", f"{task_id}: {intent}",
        )
        runtime.recovery_ledger.start_run(work_id, f"{task_id}: {intent}")
        first = runtime.recovery_ledger.begin_attempt(
            work_id, "execution", "execution-failure", "default-plan", action="reproduce",
        )
        runtime.recovery_ledger.finish_attempt(first, outcome="failed")
        checkpoint = runtime.recovery_ledger.checkpoint(
            work_id, "execution", status="pending", head_sha="",
            payload={"iteration": 1, "hypothesis": "default-plan", "recovery_mode": "initial"},
        )
        runtime.recovery_decision_journal.record(
            work_id=work_id, phase="execution", attempt_number=2, action="retry",
            strategy_id=None, failure_class="execution_failure", attempts_remaining=1,
            checkpoint_digest=runtime.recovery_decision_journal._checkpoint_digest(checkpoint),
            side_effect_state="ambiguous", reason_code="bounded_retry",
        )
        calls = []
        runtime._run_once = lambda **_kwargs: calls.append("replayed") or None

        with self.assertRaisesRegex(RuntimeError, "pending_decision"):
            runtime.run(
                session_id=session_id, task_id=task_id, project_root=self.root,
                intent=intent, context={"auren_max_iterations": 2},
            )
        self.assertEqual([], calls, "the interrupted decision must not dispatch another attempt")

    def test_journal_persistence_failure_stops_before_another_recovery_attempt(self):
        graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda state: "ok")])
        runtime = AdaptiveRuntime(graph, state_root=self.root / "journal-failure-state")
        observed = []

        def failing_execution(**kwargs):
            observed.append(dict(kwargs.get("context") or {}))
            raise RuntimeError("repeatable failure")

        runtime._run_once = failing_execution

        def fail_receipt_write(**_kwargs):
            raise OSError("simulated journal storage outage")

        runtime.recovery_decision_journal.record = fail_receipt_write
        with self.assertRaisesRegex(RuntimeError, "stopped safely"):
            runtime.run(
                session_id="journal-failure-session",
                task_id="journal-failure-task",
                project_root=self.root,
                intent="never continue recovery without its durable decision receipt",
                context={"auren_max_iterations": 3},
            )

        self.assertEqual(1, len(observed), "must stop before dispatching another attempt")
        with sqlite3.connect(runtime.execution_controller.db_path) as db:
            work_id = db.execute(
                "SELECT work_unit_id FROM sessions WHERE session_id=?",
                ("journal-failure-session:journal-failure-task",),
            ).fetchone()[0]
        attempts = runtime.recovery_ledger.attempts(work_id)
        self.assertEqual(1, len(attempts))
        self.assertEqual("failed", attempts[0].outcome)

    def test_real_adaptive_graph_uses_supervisor_owned_guard(self):
        seen = []
        graph = Graph([
            Node(
                "work",
                NodeKind.DETERMINISTIC,
                lambda state: seen.append(state["_auren_runtime_execution_guard"]) or "ok",
            )
        ])
        runtime = AdaptiveRuntime(graph, state_root=self.root / "real-shared-guard-state")
        result = runtime.run(
            session_id="real-shared-guard-session",
            task_id="real-shared-guard-task",
            project_root=self.root,
            intent="verify real graph shares supervisor budget",
            enrich_context=False,
            enrich_cognition=False,
        )
        self.assertEqual("accepted", result.status.value)
        self.assertEqual(1, len(seen))
        self.assertEqual(2, seen[0].attempts_used)  # supervisor iteration plus graph node

        with sqlite3.connect(runtime.execution_controller.db_path) as db:
            work_id = db.execute(
                "SELECT work_unit_id FROM sessions WHERE session_id=?",
                ("real-shared-guard-session:real-shared-guard-task",),
            ).fetchone()[0]
        checkpoint = runtime.recovery_ledger.get_checkpoint(work_id, "execution")
        self.assertIsNotNone(checkpoint)
        self.assertEqual(2, checkpoint.payload["runtime_guard"]["attempts_used"])

    def test_harness_experiment_checkpoint_preserves_runtime_guard(self):
        observed = []
        def run_node(state):
            state["auren_harness_experiment_persist"]({
                "schema_version": "auren-harness-experiment-checkpoint-v1",
                "run_id": "integration-run",
                "stage": "candidate_probe",
                "status": "in_progress",
                "manifest_digest": "a" * 64,
            })
            observed.append(state["_auren_runtime_execution_guard"])
            return "ok"

        runtime = AdaptiveRuntime(
            Graph([Node("work", NodeKind.DETERMINISTIC, run_node)]),
            state_root=self.root / "harness-experiment-checkpoint-state",
        )
        result = runtime.run(
            session_id="harness-experiment-session",
            task_id="harness-experiment-task",
            project_root=self.root,
            intent="preserve experiment checkpoint with canonical guard",
            enrich_context=False,
            enrich_cognition=False,
        )
        self.assertEqual("accepted", result.status.value)
        self.assertEqual(1, len(observed))
        with sqlite3.connect(runtime.execution_controller.db_path) as db:
            work_id = db.execute(
                "SELECT work_unit_id FROM sessions WHERE session_id=?",
                ("harness-experiment-session:harness-experiment-task",),
            ).fetchone()[0]
        checkpoint = runtime.recovery_ledger.get_checkpoint(work_id, "execution")
        self.assertIsNotNone(checkpoint)
        self.assertIn("runtime_guard", checkpoint.payload)
        self.assertEqual("integration-run", checkpoint.payload["harness_experiment"]["run_id"])
        restored = RuntimeExecutionGuard.resume(checkpoint.payload["runtime_guard"])
        self.assertEqual(observed[0].attempts_used, restored.attempts_used)

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
        restored = RuntimeExecutionGuard.resume(checkpoint.payload["runtime_guard"])
        self.assertEqual(6, restored.attempts_used)
        self.assertEqual(
            checkpoint.payload["runtime_guard"]["charged_operations"],
            restored.snapshot()["charged_operations"],
        )

    def test_interrupted_provider_charge_survives_restart_without_replay(self):
        graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda state: "ok")])
        state_root = self.root / "interrupted-provider-state"
        runtime = AdaptiveRuntime(graph, state_root=state_root)
        session_id = "interrupted-provider-session"
        task_id = "interrupted-provider-task"
        intent = "persist provider charge before dispatch and never replay incomplete work"
        operation_key = "provider-call:stable-request-1"
        calls = []

        class SimulatedProcessInterruption(BaseException):
            pass

        def interrupted_execution(**kwargs):
            calls.append("first-process")
            context = dict(kwargs.get("context") or {})
            guard = context["_auren_runtime_execution_guard"]
            guard.charge_attempt(operation_key)
            context["auren_runtime_guard_persist"](guard.snapshot())
            # Simulate abrupt process loss after the durable pre-dispatch receipt.
            raise SimulatedProcessInterruption()

        runtime._run_once = interrupted_execution
        with self.assertRaises(SimulatedProcessInterruption):
            runtime.run(
                session_id=session_id,
                task_id=task_id,
                project_root=self.root,
                intent=intent,
                enrich_context=False,
                enrich_cognition=False,
            )

        with sqlite3.connect(runtime.execution_controller.db_path) as db:
            work_id = db.execute(
                "SELECT work_unit_id FROM sessions WHERE session_id=?",
                (f"{session_id}:{task_id}",),
            ).fetchone()[0]
        interrupted_checkpoint = runtime.recovery_ledger.get_checkpoint(work_id, "execution")
        self.assertIsNotNone(interrupted_checkpoint)
        self.assertEqual("in_progress", interrupted_checkpoint.status)
        charged = interrupted_checkpoint.payload["runtime_guard"]["charged_operations"]
        self.assertIn(operation_key, charged)
        self.assertEqual(len(charged), len(set(charged)))

        resumed = AdaptiveRuntime(graph, state_root=state_root)
        resumed._run_once = lambda **_kwargs: calls.append("replayed") or None
        with self.assertRaisesRegex(RuntimeError, "explicit recovery is required"):
            resumed.run(
                session_id=session_id,
                task_id=task_id,
                project_root=self.root,
                intent=intent,
                enrich_context=False,
                enrich_cognition=False,
            )
        self.assertEqual(["first-process"], calls)
        restored_guard = RuntimeExecutionGuard.resume(interrupted_checkpoint.payload["runtime_guard"])
        self.assertFalse(restored_guard.charge_attempt(operation_key))
        self.assertEqual(charged, restored_guard.snapshot()["charged_operations"])

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

    def test_runtime_requires_explicit_recovery_for_in_progress_checkpoint_without_graph_state(self):
        graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda state: "ok")])
        runtime = AdaptiveRuntime(graph, state_root=self.root / "incomplete-checkpoint-state")
        session_id = "incomplete-checkpoint-session"
        task_id = "incomplete-checkpoint-task"
        intent = "do not restart an incomplete graph without a state envelope"
        work_id = runtime.execution_controller.work_unit_for_session(
            f"{session_id}:{task_id}", f"{task_id}: {intent}"
        )
        # Checkpoints are attached to an existing durable run, not allowed to
        # manufacture one implicitly. Model a process interruption after run start.
        runtime.recovery_ledger.start_run(work_id, f"{task_id}: {intent}")
        runtime.recovery_ledger.checkpoint(
            work_id, "execution", status="in_progress", head_sha="",
            payload={"iteration": 1, "deadline_at": "2026-10-18T12:00:00+00:00"},
        )

        with self.assertRaisesRegex(RuntimeError, "explicit recovery is required"):
            runtime.run(
                session_id=session_id,
                task_id=task_id,
                project_root=self.root,
                intent=intent,
                enrich_context=False,
                enrich_cognition=False,
            )

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
