from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.engineering_context_packet import build_context_packet
from portable.engineering_gate_dispatch import build_gate_engineering_plan
from portable.engineering_plan_dispatch import EngineeringPlanDispatch
from portable.engineering_plan_recovery import EngineeringPlanRecoveryBridge
from portable.engineering_plan_store import EngineeringPlanStore
from portable.engineering_recovery import EngineeringRecoveryLedger
from portable.execution_controller import ExecutionController


def gate_dispatch_test_handler(payload):
    return {"accepted": True, "gate_id": payload["gate_id"]}


class EngineeringGateDispatchPlanTests(unittest.TestCase):
    def test_gate_dag_compiles_to_canonical_plan_with_only_roots_ready(self):
        plan, run = build_gate_engineering_plan(
            run_id="gate-run-1", source_revision="a" * 40,
            graph_digest="graph-sha", intent_digest="intent-sha", agents={},
        )
        statuses = {item.task_id: item.status for item in run.tasks}
        self.assertEqual(9, len(plan.tasks))
        self.assertEqual("ready", statuses["post_merge_evidence"])
        self.assertTrue(all(
            status == "pending"
            for task_id, status in statuses.items()
            if task_id != "post_merge_evidence"
        ))
        durable = next(task for task in plan.tasks if task.task_id == "runtime_isolation_reconciliation")
        self.assertIn("durable_plan_dispatch", durable.depends_on)
        self.assertEqual("critical", durable.risk)
        self.assertTrue(any("Gate crew-plan digest:" in item for item in plan.requirements))

    def test_gate_plan_is_deterministic_for_identical_inputs(self):
        kwargs = dict(
            run_id="gate-run-2", source_revision="b" * 40,
            graph_digest="graph-sha", intent_digest="intent-sha", agents={},
        )
        first, first_run = build_gate_engineering_plan(**kwargs)
        second, second_run = build_gate_engineering_plan(**kwargs)
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first_run.digest, second_run.digest)

    def test_root_gate_uses_canonical_checkpoint_and_dispatch_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, _ = build_gate_engineering_plan(
                run_id="gate-run-integration", source_revision="d" * 40,
                graph_digest="graph-sha", intent_digest="intent-sha", agents={},
            )
            store = EngineeringPlanStore(Path(tmp) / "plans.sqlite3")
            ledger = EngineeringRecoveryLedger(Path(tmp) / "runtime" / ".auren" / "recovery.sqlite3")
            recovery = EngineeringPlanRecoveryBridge(store=store, ledger=ledger)
            run = recovery.start(plan)
            run = recovery.transition_task(
                plan.run_id, "post_merge_evidence", "ready",
                reason="root gate is eligible for dispatch",
            )
            controller = ExecutionController(Path(tmp) / "runtime")
            controller.register_handler(
                "gate-dispatch-test", gate_dispatch_test_handler, execution_class="read_only",
            )
            dispatcher = EngineeringPlanDispatch(
                store=store, controller=controller, recovery_bridge=recovery,
            )
            packet = build_context_packet(
                plan=plan, run=run, task_id="post_merge_evidence",
                policy_constraints=("Do not bypass evidence or review gates.",),
                budget_remaining=dict(plan.budget),
                next_safe_action="Run post-merge checks and collect immutable evidence.",
            )
            first = dispatcher.dispatch(
                run_id=plan.run_id, task_id="post_merge_evidence",
                dispatch_key="gate-run-integration:post_merge_evidence:r1",
                handler="gate-dispatch-test", packet=packet,
                payload={"gate_id": "post_merge_evidence"},
            )
            replay = dispatcher.dispatch(
                run_id=plan.run_id, task_id="post_merge_evidence",
                dispatch_key="gate-run-integration:post_merge_evidence:r1",
                handler="gate-dispatch-test", packet=packet,
                payload={"gate_id": "post_merge_evidence"},
            )
            self.assertEqual(first.id, replay.id)
            self.assertEqual("pending", controller.task(first.id).state)

    def test_plan_preserves_dependencies_as_authoritative_task_edges(self):
        plan, _ = build_gate_engineering_plan(
            run_id="gate-run-3", source_revision="c" * 40,
            graph_digest="graph-sha", intent_digest="intent-sha", agents={},
        )
        by_id = {task.task_id: task for task in plan.tasks}
        self.assertEqual(
            ("runtime_isolation_reconciliation", "provenance_learning", "safe_parallel_execution"),
            by_id["guarded_promotion"].depends_on,
        )
        self.assertIn("operator-approved rollback procedure", by_id["guarded_promotion"].rollback_plan)


if __name__ == "__main__":
    unittest.main()
