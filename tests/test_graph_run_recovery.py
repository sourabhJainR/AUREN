from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from pathlib import Path

from portable.agency_evolution_gates import ImprovementJournal
from portable.dependency_aware_fanout import DependencyAwareFanout
from portable.graph_run_recovery import GraphCheckpointMismatch, GraphRunRecoveryBridge
from portable.runtime_recovery import EngineeringRecoveryLedger, RuntimeExecutionGuard
from portable.task_planner import Task, TaskPlan


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class GraphRunRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plan = TaskPlan([
            Task("root", "Root node"),
            Task("child", "Dependent node", dependencies=["root"]),
        ])
        self.ledger_path = self.root / "recovery.db"
        self.journal_path = self.root / "graph.jsonl"
        self.guard = RuntimeExecutionGuard(max_steps=20, max_attempts=10)
        self.bridge = self.make_bridge()
        self.bridge.start(budget_snapshot=self.guard.snapshot())

    def tearDown(self):
        self.temp.cleanup()

    def make_bridge(self):
        return GraphRunRecoveryBridge(
            run_id="graph-run-1", plan=self.plan,
            intent_digest=sha("immutable graph intent"),
            source_revision="a" * 40,
            ledger=EngineeringRecoveryLedger(self.ledger_path),
            journal=ImprovementJournal(self.journal_path),
        )

    def working(self, node_id: str, event_id: str):
        self.guard.charge_step()
        self.guard.charge_attempt(f"{node_id}:attempt:{event_id}")
        return self.bridge.record_node(
            event_id=event_id, node_id=node_id, status="working",
            budget_snapshot=self.guard.snapshot(), worker_id=f"worker-{node_id}",
            workspace_id=f"workspace-{node_id}", attempts=1,
            next_safe_action=f"execute:{node_id}",
        )

    def test_initial_dispatch_uses_canonical_dependencies_and_restart_preserves_state(self):
        self.assertEqual(self.bridge.dispatchable_nodes(), ("root",))
        with self.assertRaisesRegex(ValueError, "before all dependencies"):
            self.bridge.record_node(
                event_id="premature-child", node_id="child", status="working",
                budget_snapshot=self.guard.snapshot(), worker_id="worker-child",
                workspace_id="workspace-child", next_safe_action="execute:child",
            )
        self.working("root", "root-start")
        restarted = self.make_bridge()
        snapshot = restarted.load()
        self.assertEqual(snapshot["nodes"]["root"]["status"], "working")
        self.assertEqual(snapshot["nodes"]["root"]["worker_id"], "worker-root")
        self.assertEqual(snapshot["nodes"]["root"]["workspace_id"], "workspace-root")
        self.assertEqual(snapshot["nodes"]["root"]["attempts"], 1)
        self.assertEqual(restarted.dispatchable_nodes(), ())

    def test_only_executed_evidence_can_complete_all_nodes_and_verify_run(self):
        self.working("root", "root-start")
        self.guard.charge_step()
        root_complete = self.bridge.record_node(
            event_id="root-complete", node_id="root", status="complete",
            budget_snapshot=self.guard.snapshot(), evidence_refs=["ci://root/1"],
            next_safe_action="schedule-child",
        )
        self.assertEqual(root_complete["run_status"], "in_progress")
        self.assertEqual(self.bridge.dispatchable_nodes(), ("child",))
        self.working("child", "child-start")
        self.guard.charge_step()
        final = self.bridge.record_node(
            event_id="child-complete", node_id="child", status="complete",
            budget_snapshot=self.guard.snapshot(), evidence_refs=["test://child/1"],
            next_safe_action="verify-final-receipt",
        )
        self.assertEqual(final["run_status"], "complete")
        checkpoint = self.bridge.ledger.get_checkpoint("graph-run-1", self.bridge.PHASE)
        self.assertEqual(checkpoint.status, "verified")
        self.assertEqual(checkpoint.payload["verified_head_sha"], "a" * 40)
        self.assertEqual(set(checkpoint.payload["verification_evidence"]), {"ci://root/1", "test://child/1"})
        self.assertEqual(self.bridge.load()["run_status"], "complete")
        self.assertEqual(self.bridge.dispatchable_nodes(), ())

    def test_duplicate_dispatch_and_dependency_order_are_rejected(self):
        self.working("root", "root-start")
        with self.assertRaisesRegex(ValueError, "invalid graph node transition"):
            self.bridge.record_node(
                event_id="duplicate-dispatch", node_id="root", status="working",
                budget_snapshot=self.guard.snapshot(), worker_id="worker-root",
                workspace_id="workspace-root", attempts=1, next_safe_action="execute:root",
            )
        with self.assertRaisesRegex(ValueError, "before all dependencies"):
            self.bridge.record_node(
                event_id="child-too-early", node_id="child", status="working",
                budget_snapshot=self.guard.snapshot(), worker_id="worker-child",
                workspace_id="workspace-child", attempts=1, next_safe_action="execute:child",
            )

    def test_budget_snapshot_cannot_refund_charged_steps_or_attempts(self):
        self.working("root", "root-start")
        fresh_guard = RuntimeExecutionGuard(max_steps=20, max_attempts=10)
        with self.assertRaisesRegex(ValueError, "budget was refunded"):
            self.bridge.record_node(
                event_id="refund-budget", node_id="root", status="blocked",
                budget_snapshot=fresh_guard.snapshot(), blockers=["operator gate"],
                next_safe_action="inspect-budget",
            )

    def test_expired_deadline_cannot_produce_node_completion(self):
        deadline_guard = RuntimeExecutionGuard(max_steps=20, max_attempts=10, deadline_seconds=0.2)
        bridge = GraphRunRecoveryBridge(
            run_id="deadline-run", plan=self.plan,
            intent_digest=sha("deadline intent"), source_revision="b" * 40,
            ledger=EngineeringRecoveryLedger(self.root / "deadline-recovery.db"),
            journal=ImprovementJournal(self.root / "deadline-graph.jsonl"),
        )
        bridge.start(budget_snapshot=deadline_guard.snapshot())
        deadline_guard.charge_step()
        deadline_guard.charge_attempt("root:deadline-attempt")
        bridge.record_node(
            event_id="root-start", node_id="root", status="working",
            budget_snapshot=deadline_guard.snapshot(), worker_id="worker-root",
            workspace_id="workspace-root", attempts=1, next_safe_action="execute:root",
        )
        time.sleep(0.25)
        with self.assertRaisesRegex(ValueError, "expired deadline"):
            bridge.record_node(
                event_id="late-completion", node_id="root", status="complete",
                budget_snapshot=deadline_guard.snapshot(), evidence_refs=["claimed://not-enough"],
                next_safe_action="inspect-deadline",
            )

    def test_worker_loss_is_unknown_and_never_projects_to_success(self):
        self.working("root", "root-start")
        unknown = self.bridge.record_node(
            event_id="worker-lost", node_id="root", status="unknown",
            budget_snapshot=self.guard.snapshot(), next_safe_action="reconcile-worker-outcome",
        )
        self.assertEqual(unknown["nodes"]["root"]["status"], "unknown")
        self.assertEqual(unknown["run_status"], "in_progress")
        self.assertEqual(self.bridge.dispatchable_nodes(), ())
        with self.assertRaisesRegex(ValueError, "invalid graph node transition"):
            self.bridge.record_node(
                event_id="false-success", node_id="root", status="complete",
                budget_snapshot=self.guard.snapshot(), evidence_refs=["worker-claimed://passed"],
                next_safe_action="verify",
            )

    def test_cancellation_preserves_unknown_termination_and_stops_dispatch(self):
        self.working("root", "root-start")
        self.guard.cancel("operator requested stop")
        cancelled = self.bridge.cancel_run(
            event_id="cancel-run", reason="operator requested stop",
            next_safe_action="reconcile-active-worker",
            budget_snapshot=self.guard.snapshot(),
        )
        self.assertEqual(cancelled["run_status"], "cancelled")
        self.assertEqual(cancelled["nodes"]["root"]["status"], "unknown")
        self.assertEqual(cancelled["nodes"]["root"]["next_safe_action"], "reconcile_worker_termination_before_resume")
        self.assertEqual(cancelled["nodes"]["child"]["status"], "cancelled")
        self.assertEqual(self.bridge.dispatchable_nodes(), ())

    def test_crash_between_journal_and_ledger_checkpoint_requires_explicit_reconciliation(self):
        original_checkpoint = self.bridge.ledger.checkpoint
        self.bridge.ledger.checkpoint = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected checkpoint crash"))
        with self.assertRaisesRegex(OSError, "injected checkpoint crash"):
            self.working("root", "crash-after-journal")
        self.bridge.ledger.checkpoint = original_checkpoint
        restarted = self.make_bridge()
        with self.assertRaisesRegex(GraphCheckpointMismatch, "advanced without a matching ledger"):
            restarted.load()
        latest = [event for event in restarted.journal.read() if event.work_id == "graph-run-1"][-1]
        snapshot_digest = latest.payload["snapshot_digest"]
        with self.assertRaisesRegex(GraphCheckpointMismatch, "operator-supplied"):
            restarted.reconcile(reason="inspect split write", expected_snapshot_digest="0" * 64)
        reconciled = restarted.reconcile(
            reason="verified the journal-ahead node checkpoint after injected crash",
            expected_snapshot_digest=snapshot_digest,
        )
        self.assertEqual(reconciled["nodes"]["root"]["status"], "working")
        self.assertEqual(restarted.load()["nodes"]["root"]["worker_id"], "worker-root")

    def test_mutating_canonical_plan_after_binding_blocks_resume(self):
        self.plan.tasks["root"].title = "mutated after checkpoint"
        with self.assertRaisesRegex(GraphCheckpointMismatch, "TaskPlan changed"):
            self.bridge.load()

    def test_checkpoint_identity_rejects_stale_plan_or_intent(self):
        stale = GraphRunRecoveryBridge(
            run_id="graph-run-1", plan=TaskPlan([Task("different", "Different")]),
            intent_digest=sha("immutable graph intent"), source_revision="a" * 40,
            ledger=EngineeringRecoveryLedger(self.ledger_path),
            journal=ImprovementJournal(self.journal_path),
        )
        with self.assertRaisesRegex(ValueError, "different immutable intent"):
            stale.start(budget_snapshot=self.guard.snapshot())


if __name__ == "__main__":
    unittest.main()
