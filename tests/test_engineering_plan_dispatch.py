from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path

from portable.engineering_context_packet import build_context_packet
from portable.engineering_plan import EngineeringPlan, PlanTask, new_plan_run
from portable.engineering_plan_dispatch import DispatchCancelled, DispatchConflict, EngineeringPlanDispatch
from portable.engineering_plan_store import EngineeringPlanStore
from portable.engineering_plan_recovery import EngineeringPlanRecoveryBridge, EngineeringPlanCheckpointMismatch
from portable.engineering_recovery import EngineeringRecoveryLedger
from portable.execution_controller import ExecutionController


DISPATCH_HANDLER_CALLS = []

def dispatch_test_handler(payload):
    """Importable test handler used to exercise the durable registration contract."""
    DISPATCH_HANDLER_CALLS.append(dict(payload))
    return {"accepted": True}


def prepared(tmp: str, *, budget=None):
    plan = EngineeringPlan(
        run_id="dispatch-run", goal="exercise durable dispatch",
        requirements=("dispatch is idempotent",), source_revision="revision-abc",
        budget=budget or {},
        tasks=(
            PlanTask("implement", "Implement bounded change", ("tests pass",), "revert bounded change"),
            PlanTask("review", "Review bounded change", ("review passes",), "revert review"),
        ),
    )
    run = new_plan_run(plan)
    run = run.transition_task("implement", "ready", plan=plan, reason="no dependencies")
    run = run.transition_task("review", "ready", plan=plan, reason="no dependencies")
    store = EngineeringPlanStore(Path(tmp) / "plans.sqlite3")
    store.create(plan, run)
    packet = build_context_packet(
        plan=plan, run=run, task_id="implement",
        policy_constraints=("Do not weaken security gates",),
        budget_remaining={"attempts": 2, "dispatches": 1},
        next_safe_action="Implement the bounded change and run its tests",
    )
    review_packet = build_context_packet(
        plan=plan, run=run, task_id="review",
        policy_constraints=("Do not weaken security gates",),
        budget_remaining={"attempts": 2, "dispatches": 1},
        next_safe_action="Review the bounded change and verify its tests",
    )
    controller = ExecutionController(Path(tmp) / "runtime")
    controller.register_handler("dispatch-test", dispatch_test_handler, execution_class="read_only")
    recovery_bridge = EngineeringPlanRecoveryBridge(
        store=store,
        ledger=EngineeringRecoveryLedger(Path(tmp) / "runtime" / ".auren" / "recovery.sqlite3"),
    )
    recovery_bridge.ledger.start_run(plan.run_id, recovery_bridge._intent(plan))
    recovery_bridge.ledger.checkpoint(
        plan.run_id, recovery_bridge.PHASE, status="in_progress",
        head_sha=plan.source_revision,
        payload=recovery_bridge._checkpoint_payload(
            plan, run, next_safe_action="schedule_dependency_ready_task",
        ),
    )
    bridge = EngineeringPlanDispatch(
        store=store, controller=controller, recovery_bridge=recovery_bridge,
    )
    return plan, run, packet, review_packet, store, controller, bridge


class EngineeringPlanDispatchTests(unittest.TestCase):
    # SQLite may keep WAL/database handles alive until controller references are
    # collected on Windows; test assertions are independent of temp-dir cleanup.
    def test_dispatch_persists_binding_and_replay_returns_same_task(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            first = controller.dispatch_engineering_plan_task(
                store=store, run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                handler="dispatch-test", packet=packet, payload={"change": "small"},
                recovery_bridge=bridge.recovery_bridge,
            )
            second = bridge.dispatch(
                run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                handler="dispatch-test", packet=packet, payload={"change": "small"},
            )
            self.assertEqual(first.id, second.id)
            self.assertEqual("pending", controller.task(first.id).state)
            self.assertEqual(plan.digest, first.payload["auren_dispatch"]["plan_digest"])
            self.assertEqual(run.revision, first.payload["auren_dispatch"]["run_revision"])
            self.assertEqual(packet.packet_digest, first.payload["auren_dispatch"]["packet_digest"])

    def test_deadline_policy_is_persisted_and_replay_bound(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            first = controller.dispatch_engineering_plan_task(
                store=store, run_id=plan.run_id, task_id="implement",
                dispatch_key="dispatch-deadline", handler="dispatch-test",
                packet=packet, recovery_bridge=bridge.recovery_bridge,
                deadline_seconds=30,
            )
            self.assertIsNotNone(first.deadline_at)
            self.assertEqual(30.0, first.payload["auren_dispatch"]["deadline_policy_seconds"])
            self.assertEqual(first.deadline_at, first.payload["auren_dispatch"]["deadline_at"])
            with sqlite3.connect(controller.db_path) as db:
                row = db.execute(
                    "SELECT deadline_at,deadline_seconds FROM tasks WHERE id=?", (first.id,)
                ).fetchone()
            self.assertEqual(first.deadline_at, row[0])
            self.assertEqual(30.0, row[1])

            replayed = bridge.dispatch(
                run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-deadline",
                handler="dispatch-test", packet=packet, deadline_seconds=30,
            )
            self.assertEqual(first.id, replayed.id)
            self.assertEqual(first.deadline_at, replayed.deadline_at)
            with self.assertRaises(DispatchConflict):
                bridge.dispatch(
                    run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-deadline",
                    handler="dispatch-test", packet=packet, deadline_seconds=31,
                )
            with self.assertRaises(DispatchConflict):
                bridge.dispatch(
                    run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-deadline",
                    handler="dispatch-test", packet=packet,
                )

    def test_invalid_plan_dispatch_deadlines_are_rejected_before_queue_insertion(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            for value in (0, -1, 86401, float("inf"), True):
                with self.subTest(deadline_seconds=value), self.assertRaises(ValueError):
                    bridge.dispatch(
                        run_id=plan.run_id, task_id="implement",
                        dispatch_key=f"invalid-deadline-{value}", handler="dispatch-test",
                        packet=packet, deadline_seconds=value,
                    )
            with sqlite3.connect(controller.db_path) as db:
                self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_controller_dispatch_fails_closed_when_canonical_checkpoint_is_missing(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, _ = prepared(tmp)
            recovery_bridge = EngineeringPlanRecoveryBridge(
                store=store,
                ledger=EngineeringRecoveryLedger(Path(tmp) / "runtime" / ".auren" / "missing-recovery.sqlite3"),
            )
            with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "checkpoint is missing"):
                controller.dispatch_engineering_plan_task(
                    store=store, run_id=plan.run_id, task_id="implement",
                    dispatch_key="dispatch-missing-checkpoint", handler="dispatch-test",
                    packet=packet, recovery_bridge=recovery_bridge,
                )
            with sqlite3.connect(controller.db_path) as db:
                count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            self.assertEqual(0, count, "a missing canonical checkpoint must block queue insertion")

    def test_controller_dispatch_rejects_bridge_for_another_plan_store(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, _ = prepared(tmp)
            other_store = EngineeringPlanStore(Path(tmp) / "other-plans.sqlite3")
            recovery_bridge = EngineeringPlanRecoveryBridge(
                store=other_store,
                ledger=EngineeringRecoveryLedger(Path(tmp) / "runtime" / ".auren" / "recovery.sqlite3"),
            )
            with self.assertRaisesRegex(ValueError, "same plan store"):
                controller.dispatch_engineering_plan_task(
                    store=store, run_id=plan.run_id, task_id="implement",
                    dispatch_key="dispatch-wrong-store", handler="dispatch-test",
                    packet=packet, recovery_bridge=recovery_bridge,
                )

    def test_controller_dispatch_accepts_matching_canonical_checkpoint(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, _ = prepared(tmp)
            recovery_bridge = EngineeringPlanRecoveryBridge(
                store=store,
                ledger=EngineeringRecoveryLedger(Path(tmp) / "runtime" / ".auren" / "recovery.sqlite3"),
            )
            recovery_bridge.ledger.start_run(plan.run_id, recovery_bridge._intent(plan))
            recovery_bridge.ledger.checkpoint(
                plan.run_id, recovery_bridge.PHASE, status="in_progress",
                head_sha=plan.source_revision,
                payload=recovery_bridge._checkpoint_payload(
                    plan, run, next_safe_action="schedule_dependency_ready_task",
                ),
            )
            task = controller.dispatch_engineering_plan_task(
                store=store, run_id=plan.run_id, task_id="implement",
                dispatch_key="dispatch-verified-checkpoint", handler="dispatch-test",
                packet=packet, recovery_bridge=recovery_bridge,
            )
            self.assertEqual("pending", task.state)
            self.assertEqual(plan.digest, task.payload["auren_dispatch"]["plan_digest"])

    def test_worker_revalidates_checkpoint_before_running_queued_handler(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            DISPATCH_HANDLER_CALLS.clear()
            task = bridge.dispatch(
                run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-worker-guard",
                handler="dispatch-test", packet=packet, payload={"change": "small"},
            )
            with bridge.recovery_bridge.ledger._db() as db:
                db.execute(
                    "DELETE FROM recovery_checkpoints WHERE task_id=? AND phase=?",
                    (plan.run_id, bridge.recovery_bridge.PHASE),
                )
            with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "checkpoint is missing"):
                controller.run_once(task.id)
            self.assertEqual([], DISPATCH_HANDLER_CALLS, "handler must not run without a valid canonical checkpoint")
            self.assertEqual("failed", controller.task(task.id).state)

    def test_worker_fails_closed_after_restart_until_trusted_bridge_is_registered(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            task = bridge.dispatch(
                run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-worker-restart",
                handler="dispatch-test", packet=packet, payload={"change": "small"},
            )
            restarted_controller = ExecutionController(Path(tmp) / "runtime")
            restarted_controller.register_handler(
                "dispatch-test", dispatch_test_handler, execution_class="read_only",
            )
            DISPATCH_HANDLER_CALLS.clear()
            with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "bridge is not registered"):
                restarted_controller.run_once(task.id)
            self.assertEqual([], DISPATCH_HANDLER_CALLS)
            self.assertEqual("failed", restarted_controller.task(task.id).state)

    def test_dispatch_replay_survives_plan_revision_advancing_after_enqueue(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            first = bridge.dispatch(
                run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                handler="dispatch-test", packet=packet, payload={"change": "small"},
            )
            advanced = bridge.recovery_bridge.transition_task(
                plan.run_id, "implement", "running", reason="worker claimed task",
            )

            replayed = bridge.dispatch(
                run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                handler="dispatch-test", packet=packet, payload={"change": "small"},
            )
            self.assertEqual(first.id, replayed.id)
            self.assertEqual("pending", controller.task(first.id).state)

    def test_replay_with_changed_packet_or_payload_fails_closed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            bridge.dispatch(
                run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                handler="dispatch-test", packet=packet, payload={"change": "small"},
            )
            changed_packet = build_context_packet(
                plan=plan, run=run, task_id="implement",
                policy_constraints=("Do not weaken security gates",),
                budget_remaining={"attempts": 2, "dispatches": 1},
                next_safe_action="Implement the bounded change and run its tests",
                optional_context=("new context",),
            )
            with self.assertRaises(DispatchConflict):
                bridge.dispatch(
                    run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                    handler="dispatch-test", packet=changed_packet, payload={"change": "small"},
                )
            with self.assertRaises(DispatchConflict):
                bridge.dispatch(
                    run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                    handler="dispatch-test", packet=packet, payload={"change": "different"},
                )

    def test_same_task_revision_cannot_get_a_second_dispatch_key(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            bridge.dispatch(
                run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                handler="dispatch-test", packet=packet,
            )
            with sqlite3.connect(controller.company.db_path) as db:
                before = db.execute("SELECT COUNT(*) FROM work_units").fetchone()[0]
            with self.assertRaises(DispatchConflict):
                bridge.dispatch(
                    run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-2",
                    handler="dispatch-test", packet=packet,
                )
            with sqlite3.connect(controller.company.db_path) as db:
                after = db.execute("SELECT COUNT(*) FROM work_units").fetchone()[0]
            self.assertEqual(before, after, "rejected duplicate must not create an orphan work unit")

    def test_nested_credential_like_payload_keys_are_rejected_before_enqueue(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            with self.assertRaisesRegex(ValueError, "credential-like"):
                bridge.dispatch(
                    run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-secret-check",
                    handler="dispatch-test", packet=packet,
                    payload={"metadata": {"service": {"api_key": "not-to-be-persisted"}}},
                )
            with sqlite3.connect(controller.db_path) as db:
                count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            self.assertEqual(0, count)

    def test_stale_packet_revision_is_rejected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            updated = bridge.recovery_bridge.transition_task(
                plan.run_id, "implement", "running", reason="worker claimed task",
            )
            with self.assertRaisesRegex(DispatchConflict, "revision is stale"):
                bridge.dispatch(
                    run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                    handler="dispatch-test", packet=packet,
                )

    def test_queued_dispatch_can_be_cancelled_idempotently(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            task = bridge.dispatch(
                run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                handler="dispatch-test", packet=packet,
            )
            self.assertTrue(bridge.cancel("dispatch-1", reason="operator cancelled before execution"))
            self.assertEqual("cancelled", controller.task(task.id).state)
            self.assertTrue(bridge.cancel("dispatch-1", reason="idempotent cancellation"))

    def test_running_dispatch_is_not_falsely_reported_cancelled(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            task = bridge.dispatch(
                run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                handler="dispatch-test", packet=packet,
            )
            claimed = controller._claim(task.id)
            self.assertIsNotNone(claimed)
            with self.assertRaises(DispatchCancelled):
                bridge.cancel("dispatch-1", reason="operator requested cancellation")
            self.assertEqual("running", controller.task(task.id).state)

    def test_shared_dispatch_budget_is_enforced_across_tasks(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, review_packet, store, controller, bridge = prepared(tmp, budget={"dispatches": 1})
            bridge.dispatch(
                run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                handler="dispatch-test", packet=packet,
            )
            with self.assertRaisesRegex(DispatchConflict, "budget is exhausted"):
                bridge.dispatch(
                    run_id=plan.run_id, task_id="review", dispatch_key="dispatch-2",
                    handler="dispatch-test", packet=review_packet,
                )

    def test_concurrent_duplicate_dispatch_leaves_no_orphan_work_unit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            with sqlite3.connect(controller.company.db_path) as db:
                baseline_units = db.execute("SELECT COUNT(*) FROM work_units").fetchone()[0]
            barrier = threading.Barrier(2)
            original_verify = bridge._verified_state

            def synchronized_verify(*args, **kwargs):
                verified = original_verify(*args, **kwargs)
                barrier.wait(timeout=5)
                return verified

            bridge._verified_state = synchronized_verify

            def submit(key):
                return bridge.dispatch(
                    run_id=plan.run_id, task_id="implement", dispatch_key=key,
                    handler="dispatch-test", packet=packet,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(submit, key) for key in ("concurrent-1", "concurrent-2")]
                outcomes = []
                for future in futures:
                    try:
                        outcomes.append(("ok", future.result(timeout=15)))
                    except DispatchConflict as exc:
                        outcomes.append(("conflict", str(exc)))
            self.assertEqual(1, sum(kind == "ok" for kind, _ in outcomes), outcomes)
            self.assertEqual(1, sum(kind == "conflict" for kind, _ in outcomes), outcomes)
            with sqlite3.connect(controller.db_path) as db:
                self.assertEqual(1, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
                self.assertEqual(1, db.execute("SELECT COUNT(*) FROM engineering_dispatch_intents").fetchone()[0])
            with sqlite3.connect(controller.company.db_path) as db:
                final_units = db.execute("SELECT COUNT(*) FROM work_units").fetchone()[0]
            self.assertEqual(baseline_units + 1, final_units, "losing concurrent dispatch must not leave an orphan work unit")

    def test_dispatch_intent_survives_controller_recreation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            first = bridge.dispatch(
                run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                handler="dispatch-test", packet=packet, payload={"change": "small"},
            )
            restarted_controller = ExecutionController(Path(tmp) / "runtime")
            restarted_controller.register_handler(
                "dispatch-test", dispatch_test_handler, execution_class="read_only",
            )
            restarted_bridge = EngineeringPlanDispatch(
                store=store, controller=restarted_controller,
                recovery_bridge=bridge.recovery_bridge,
            )
            resumed = restarted_bridge.dispatch(
                run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                handler="dispatch-test", packet=packet, payload={"change": "small"},
            )
            self.assertEqual(first.id, resumed.id)
            self.assertEqual("pending", resumed.state)


if __name__ == "__main__":
    unittest.main()
