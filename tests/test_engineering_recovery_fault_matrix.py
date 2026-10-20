from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from portable.engineering_context_packet import build_context_packet
from portable.engineering_plan import EngineeringPlan, PlanTask, new_plan_run
from portable.engineering_plan_dispatch import EngineeringPlanDispatch
from portable.engineering_plan_recovery import EngineeringPlanCheckpointMismatch, EngineeringPlanRecoveryBridge
from portable.engineering_plan_store import EngineeringPlanStore
from portable.engineering_recovery import EngineeringRecoveryLedger
from portable.execution_controller import ExecutionController


def recovery_matrix_handler(_payload):
    return {"accepted": True}


class EngineeringRecoveryFaultMatrixTests(unittest.TestCase):
    def _prepared_dispatch(self, root: Path):
        plan = EngineeringPlan(
            run_id="recovery-matrix-run", goal="exercise recovery fault matrix",
            requirements=("canonical checkpoint must match durable plan state",),
            source_revision="a" * 40, budget={"attempts": 3, "dispatches": 3},
            tasks=(PlanTask("work", "Execute bounded work", ("result verified",), "revert bounded work"),),
        )
        store = EngineeringPlanStore(root / "plans.sqlite3")
        ledger = EngineeringRecoveryLedger(root / "runtime" / ".auren" / "recovery.sqlite3")
        bridge = EngineeringPlanRecoveryBridge(store=store, ledger=ledger)
        bridge.start(plan)
        run = bridge.transition_task(plan.run_id, "work", "ready", reason="root task is eligible")
        packet = build_context_packet(
            plan=plan, run=run, task_id="work",
            policy_constraints=("Do not bypass evidence or review gates.",),
            budget_remaining={"attempts": 3, "dispatches": 3},
            next_safe_action="Run bounded recovery matrix test.",
        )
        controller = ExecutionController(root / "runtime")
        controller.register_handler("recovery-matrix-handler", recovery_matrix_handler, execution_class="read_only")
        dispatcher = EngineeringPlanDispatch(store=store, controller=controller, recovery_bridge=bridge)
        return plan, run, store, bridge, packet, controller, dispatcher

    def test_concurrent_identical_dispatches_return_one_durable_task(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            plan, _run, _store, _bridge, packet, controller, dispatcher = self._prepared_dispatch(Path(tmp))
            def dispatch_once():
                return dispatcher.dispatch(
                    run_id=plan.run_id, task_id="work", dispatch_key="recovery-matrix:work:r1",
                    handler="recovery-matrix-handler", packet=packet, payload={"operation": "test"},
                )
            with ThreadPoolExecutor(max_workers=2) as pool:
                first, second = list(pool.map(lambda _n: dispatch_once(), range(2)))
            self.assertEqual(first.id, second.id)
            with __import__("sqlite3").connect(controller.db_path) as db:
                count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            self.assertEqual(1, count)

    def test_restart_refuses_plan_run_when_checkpoint_digest_is_stale(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            plan, _run, store, bridge, _packet, _controller, _dispatcher = self._prepared_dispatch(root)
            stored_plan, current = store.load(plan.run_id)
            changed = current.transition_task(
                "work", "ready", plan=stored_plan, reason="simulated write before checkpoint update",
            )
            store.save_run(plan.run_id, changed, expected_revision=current.revision)
            restarted = EngineeringPlanRecoveryBridge(
                store=EngineeringPlanStore(root / "plans.sqlite3"),
                ledger=EngineeringRecoveryLedger(root / "runtime" / ".auren" / "recovery.sqlite3"),
            )
            with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "run_digest"):
                restarted.load_verified(plan.run_id)

    def test_cancellation_during_active_handler_is_observed_after_safe_batch_boundary(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            controller = ExecutionController(Path(tmp))
            entered = threading.Event()
            release = threading.Event()
            stop = threading.Event()
            def blocking_handler(_payload):
                entered.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("test coordinator did not release active handler")
                return {"accepted": True}
            controller.register_handler("recovery-blocking-handler", blocking_handler, execution_class="read_only")
            controller.submit("recovery-blocking-handler", {"operation": "bounded-test"})
            def request_stop():
                if entered.wait(timeout=5):
                    stop.set()
                    release.set()
            coordinator = threading.Thread(target=request_stop, daemon=True)
            coordinator.start()
            report = controller.run_continuously(stop_event=stop, max_cycles=3, idle_sleep_seconds=0)
            coordinator.join(timeout=5)
            self.assertTrue(stop.is_set(), "stop request must arrive while the handler is active")
            self.assertEqual("cancelled", report.stop_reason)
            self.assertEqual(1, report.completed_tasks)
            self.assertEqual(0, report.failed_tasks)


if __name__ == "__main__":
    unittest.main()
