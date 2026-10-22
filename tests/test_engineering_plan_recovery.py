from __future__ import annotations

import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path

from portable.engineering_plan import EngineeringPlan, PlanTask
from portable.engineering_plan_recovery import (
    EngineeringPlanCheckpointMismatch, EngineeringPlanRecoveryBridge,
)
from portable.engineering_plan_store import EngineeringPlanStore
from portable.engineering_recovery import EngineeringRecoveryLedger


def sample_plan() -> EngineeringPlan:
    return EngineeringPlan(
        run_id="bridge-run", goal="Complete a verified engineering task",
        requirements=("task state survives restart",),
        source_revision="source-revision-abc",
        tasks=(PlanTask(
            "implement", "Implement safely",
            ("tests pass",), "revert implementation",
        ),),
    )


def _crash_after_plan_store_commit(plan_store_path: str, ledger_path: str) -> None:
    plan = sample_plan()
    store = EngineeringPlanStore(plan_store_path)
    ledger = EngineeringRecoveryLedger(ledger_path)
    bridge = EngineeringPlanRecoveryBridge(store=store, ledger=ledger)
    current = bridge.start(plan)
    advanced = current.transition_task("implement", "ready", plan=plan, reason="ready before crash")
    store.save_run(plan.run_id, advanced, expected_revision=current.revision)
    os._exit(73)


def _crash_during_bridge_checkpoint(plan_store_path: str, ledger_path: str) -> None:
    plan = sample_plan()
    bridge = EngineeringPlanRecoveryBridge(
        store=EngineeringPlanStore(plan_store_path),
        ledger=EngineeringRecoveryLedger(ledger_path),
    )
    bridge.start(plan)
    original_checkpoint = bridge.ledger.checkpoint

    def crash_before_checkpoint(*args, **kwargs):
        # The bridge has already committed the plan/run CAS update when it calls
        # this method. Exit without Python cleanup to model abrupt process death.
        os._exit(74)

    bridge.ledger.checkpoint = crash_before_checkpoint
    bridge.transition_task(
        plan.run_id, "implement", "ready", reason="crash after durable plan CAS",
    )
    # The checkpoint must never return in this fixture.
    original_checkpoint  # keep the saved bound method referenced for clarity
    os._exit(75)


class EngineeringPlanRecoveryBridgeTests(unittest.TestCase):
    def make_bridge(self, root: Path) -> EngineeringPlanRecoveryBridge:
        return EngineeringPlanRecoveryBridge(
            store=EngineeringPlanStore(root / "plan-store.db"),
            ledger=EngineeringRecoveryLedger(root / "recovery-ledger.db"),
        )

    def test_plan_run_is_bound_to_canonical_recovery_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self.make_bridge(Path(tmp))
            plan = sample_plan()
            initial = bridge.start(plan)
            loaded_plan, loaded_run = bridge.load_verified(plan.run_id)
            self.assertEqual(plan.digest, loaded_plan.digest)
            self.assertEqual(initial.digest, loaded_run.digest)
            checkpoint = bridge.ledger.get_checkpoint(plan.run_id, bridge.PHASE)
            self.assertEqual("in_progress", checkpoint.status)
            self.assertEqual(initial.digest, checkpoint.payload["run_digest"])
            self.assertEqual(initial.revision, checkpoint.payload["run_revision"])

    def test_evidence_gated_task_completion_publishes_verified_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self.make_bridge(Path(tmp))
            plan = sample_plan()
            bridge.start(plan)
            bridge.transition_task(plan.run_id, "implement", "ready", reason="no dependencies")
            bridge.transition_task(plan.run_id, "implement", "running", reason="executor started")
            bridge.transition_task(plan.run_id, "implement", "verifying", reason="implementation done")
            final = bridge.transition_task(
                plan.run_id, "implement", "passed", reason="tests verified",
                evidence_refs=("ci://run/123",),
            )
            loaded_plan, loaded_run = bridge.load_verified(plan.run_id)
            checkpoint = bridge.ledger.get_checkpoint(plan.run_id, bridge.PHASE)
            self.assertEqual("passed", loaded_run.tasks[0].status)
            self.assertEqual(final.digest, loaded_run.digest)
            self.assertEqual("verified", checkpoint.status)
            self.assertEqual(plan.source_revision, checkpoint.payload["verified_head_sha"])
            self.assertEqual(["ci://run/123"], checkpoint.payload["verification_evidence"])

    def test_process_crash_between_plan_store_and_recovery_checkpoint_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_store_path = str(root / "plan-store.db")
            ledger_path = str(root / "recovery-ledger.db")
            process = multiprocessing.get_context("spawn").Process(
                target=_crash_after_plan_store_commit,
                args=(plan_store_path, ledger_path),
            )
            process.start()
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join(3)
                self.fail("cross-store crash fixture exceeded its time budget")
            self.assertEqual(73, process.exitcode)

            bridge = EngineeringPlanRecoveryBridge(
                store=EngineeringPlanStore(plan_store_path),
                ledger=EngineeringRecoveryLedger(ledger_path),
            )
            plan = sample_plan()
            with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "run_digest"):
                bridge.load_verified(plan.run_id)

            reconciled = bridge.reconcile_checkpoint(
                plan.run_id,
                reason="operator verified plan-store commit after simulated process crash",
                expected_plan_digest=plan.digest,
            )
            _, loaded_run = bridge.load_verified(plan.run_id)
            self.assertEqual(reconciled.digest, loaded_run.digest)
            self.assertEqual("ready", loaded_run.tasks[0].status)


    def test_bridge_transition_process_death_after_store_commit_requires_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_store_path = str(root / "transition-plan-store.db")
            ledger_path = str(root / "transition-recovery-ledger.db")
            process = multiprocessing.get_context("spawn").Process(
                target=_crash_during_bridge_checkpoint,
                args=(plan_store_path, ledger_path),
            )
            process.start()
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join(3)
                self.fail("bridge transition crash fixture exceeded its time budget")
            self.assertEqual(74, process.exitcode)

            plan = sample_plan()
            restarted = EngineeringPlanRecoveryBridge(
                store=EngineeringPlanStore(plan_store_path),
                ledger=EngineeringRecoveryLedger(ledger_path),
            )
            with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "run_digest"):
                restarted.load_verified(plan.run_id)

            reconciled = restarted.reconcile_checkpoint(
                plan.run_id,
                reason="operator verified committed CAS transition after interrupted checkpoint publication",
                expected_plan_digest=plan.digest,
            )
            _, loaded = restarted.load_verified(plan.run_id)
            self.assertEqual(reconciled.digest, loaded.digest)
            self.assertEqual("ready", loaded.tasks[0].status)

    def test_store_checkpoint_split_write_fails_closed_until_explicit_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self.make_bridge(Path(tmp))
            plan = sample_plan()
            current = bridge.start(plan)
            advanced = current.transition_task("implement", "ready", plan=plan, reason="ready")
            bridge.store.save_run(plan.run_id, advanced, expected_revision=current.revision)
            with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "run_digest"):
                bridge.load_verified(plan.run_id)
            with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "operator-supplied"):
                bridge.reconcile_checkpoint(
                    plan.run_id, reason="inspect mismatch", expected_plan_digest="0" * 64,
                )
            reconciled = bridge.reconcile_checkpoint(
                plan.run_id, reason="verified the plan-store revision after interrupted checkpoint write",
                expected_plan_digest=plan.digest,
            )
            self.assertEqual(advanced.digest, reconciled.digest)
            _, loaded = bridge.load_verified(plan.run_id)
            self.assertEqual(advanced.digest, loaded.digest)

    def test_missing_checkpoint_is_not_treated_as_fresh_or_successful_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self.make_bridge(Path(tmp))
            plan = sample_plan()
            bridge.store.create(plan)
            bridge.ledger.start_run(plan.run_id, bridge._intent(plan))
            with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "checkpoint is missing"):
                bridge.load_verified(plan.run_id)

    def test_start_does_not_silently_recreate_missing_checkpoint_for_existing_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self.make_bridge(Path(tmp))
            plan = sample_plan()
            bridge.store.create(plan)
            with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "explicit reconciliation"):
                bridge.start(plan)

    def test_blocked_or_rolled_back_checkpoint_cannot_be_reopened_by_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self.make_bridge(Path(tmp))
            plan = sample_plan()
            bridge.start(plan)
            bridge.ledger.checkpoint(
                plan.run_id, bridge.PHASE, status="blocked",
                head_sha=plan.source_revision,
                payload={
                    "contract": "auren-engineering-plan-checkpoint-v1",
                    "plan_digest": plan.digest,
                    "run_digest": bridge.store.load(plan.run_id)[1].digest,
                    "run_revision": 0,
                    "source_revision": plan.source_revision,
                    "store_path": str(bridge.store.database_path.resolve()),
                    "next_safe_action": "operator review",
                },
            )
            with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "blocked"):
                bridge.start(plan)
            with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "blocked"):
                bridge.reconcile_checkpoint(
                    plan.run_id, reason="do not bypass blocked gate", expected_plan_digest=plan.digest,
                )

    def test_checkpoint_cannot_be_replayed_against_a_different_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = self.make_bridge(Path(tmp))
            plan = sample_plan()
            bridge.start(plan)
            different = EngineeringPlan(
                run_id=plan.run_id, goal=plan.goal, requirements=("changed requirement",),
                source_revision=plan.source_revision, tasks=plan.tasks,
            )
            with self.assertRaisesRegex(ValueError, "different immutable engineering plan"):
                bridge.start(different)


if __name__ == "__main__":
    unittest.main()
