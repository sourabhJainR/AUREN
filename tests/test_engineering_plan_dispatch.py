from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.engineering_context_packet import build_context_packet
from portable.engineering_plan import EngineeringPlan, PlanTask, new_plan_run
from portable.engineering_plan_dispatch import DispatchCancelled, DispatchConflict, EngineeringPlanDispatch
from portable.engineering_plan_store import EngineeringPlanStore
from portable.execution_controller import ExecutionController


def dispatch_test_handler(payload):
    """Importable test handler used to exercise the durable registration contract."""
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
    bridge = EngineeringPlanDispatch(store=store, controller=controller)
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
            with self.assertRaises(DispatchConflict):
                bridge.dispatch(
                    run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-2",
                    handler="dispatch-test", packet=packet,
                )

    def test_stale_packet_revision_is_rejected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, run, packet, _, store, controller, bridge = prepared(tmp)
            updated = run.transition_task("implement", "running", plan=plan, reason="worker claimed task")
            store.save_run(plan.run_id, updated, expected_revision=run.revision)
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
            restarted_bridge = EngineeringPlanDispatch(store=store, controller=restarted_controller)
            resumed = restarted_bridge.dispatch(
                run_id=plan.run_id, task_id="implement", dispatch_key="dispatch-1",
                handler="dispatch-test", packet=packet, payload={"change": "small"},
            )
            self.assertEqual(first.id, resumed.id)
            self.assertEqual("pending", resumed.state)


if __name__ == "__main__":
    unittest.main()
