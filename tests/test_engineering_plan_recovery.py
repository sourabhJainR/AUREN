from __future__ import annotations

from contextlib import closing
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
    def crash_before_checkpoint(*args, **kwargs):
        # The bridge has already committed the plan/run CAS update when it calls
        # this method. Exit without Python cleanup to model abrupt process death.
        os._exit(74)

    bridge.ledger.checkpoint = crash_before_checkpoint
    bridge.transition_task(
        plan.run_id, "implement", "ready", reason="crash after durable plan CAS",
    )
    # The checkpoint must never return in this fixture.
    os._exit(75)



def _crash_after_integrated_plan_store_commit(
    root_path: str, work_key: str, evidence_refs: tuple[str, ...],
) -> None:
    """Commit the plan/run revision and terminate before its recovery checkpoint is published."""
    state_dir = Path(root_path) / ".auren"
    store = EngineeringPlanStore(state_dir / "plans.sqlite3")
    plan, current = store.load(work_key)
    advanced = current.transition_task(
        "learn-and-deliver", "passed", plan=plan,
        reason="fault injection: runtime, receipt, evidence, and learning records were inspected",
        evidence_refs=evidence_refs,
    )
    store.save_run(work_key, advanced, expected_revision=current.revision)
    os._exit(91)


def _continuous_engineering_marker_handler(payload):
    marker = Path(payload["marker_path"])
    with marker.open("a", encoding="utf-8") as stream:
        stream.write("called\n")
    return {"success": True, "evidence": [str(payload["work_key"])]}


def _crash_after_integrated_terminal_receipt_before_evidence(
    root_path: str, work_key: str, goal: str,
) -> None:
    """Exit after task and work-unit receipt commit but before terminal evidence capture."""
    from portable.execution_controller import ExecutionController

    root = Path(root_path)
    payload = {
        "work_key": work_key,
        "marker_path": str(root / "handler-calls.log"),
    }
    controller = ExecutionController(root)
    controller.register_handler(
        "continuous-engineering-marker",
        _continuous_engineering_marker_handler,
        execution_class="read_only",
    )
    task = controller.submit(
        "continuous-engineering-marker", payload, goal=goal, work_key=work_key,
    )
    original_capture = controller._capture_event

    def crash_before_terminal_evidence(event_type, *args, **kwargs):
        if event_type == "task_completed":
            os._exit(93)
        return original_capture(event_type, *args, **kwargs)

    controller._capture_event = crash_before_terminal_evidence
    controller.run_once(task.id)
    os._exit(94)


def _crash_after_integrated_task_terminal_before_work_unit_receipt(
    root_path: str, work_key: str, goal: str,
) -> None:
    """Exit after the task row commits terminal state but before its work-unit receipt."""
    from portable.execution_controller import ExecutionController

    root = Path(root_path)
    payload = {
        "work_key": work_key,
        "marker_path": str(root / "handler-calls.log"),
    }
    controller = ExecutionController(root)
    controller.register_handler(
        "continuous-engineering-marker",
        _continuous_engineering_marker_handler,
        execution_class="read_only",
    )
    task = controller.submit(
        "continuous-engineering-marker", payload, goal=goal, work_key=work_key,
    )
    original_record = controller.company.record

    def crash_before_receipt(work_unit_id, stage, status, *args, **kwargs):
        if stage == "execution" and status == "completed":
            # The task row has committed; kill the process before the separate
            # executive work-unit receipt can commit.
            os._exit(95)
        return original_record(work_unit_id, stage, status, *args, **kwargs)

    controller.company.record = crash_before_receipt
    controller.run_once(task.id)
    os._exit(96)


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



def _continuous_engineering_integration_handler(payload):
    return {"success": True, "evidence": [str(payload["work_key"])]}


def _run_integrated_harness_experiment(*, work_key, plan, ledger, guard, stop_mode=None):
    import time
    from portable.harness_experiments import (
        HarnessExperimentController, HarnessExperimentManifest, canonical_digest,
    )

    base = {"tool_guidance": {"mode": "safe"}}
    task = "verify a bounded harness improvement"
    manifest = HarnessExperimentManifest(
        schema_version="auren-harness-experiment-v1",
        run_id=work_key,
        task_digest=canonical_digest({"task": task}),
        base_harness_digest=canonical_digest(base),
        executor_id="integration-isolated-executor",
        evaluator_id="integration-independent-judge",
        seed=7,
        max_candidates=1,
        max_depth=0,
        isolation_profile="integration-restricted-profile",
        policy_digest="a" * 64,
        environment_digest="b" * 64,
    )
    adopted = []

    def persist(snapshot):
        snapshot_copy = dict(snapshot)
        if snapshot_copy.get("stage") == "complete" and snapshot_copy.get("status") == "adopted":
            checkpoint_status = "verified"
        elif snapshot_copy.get("status") in {"blocked", "unscored", "failed", "rejected"}:
            checkpoint_status = "blocked"
        else:
            checkpoint_status = "in_progress"
        payload = {
            "contract": "auren-learning-checkpoint-binding-v1",
            "work_key": work_key,
            "plan_digest": plan.digest,
            "source_revision": plan.source_revision,
            "manifest_digest": manifest.digest,
            "experiment_snapshot": snapshot_copy,
            "next_safe_action": (
                "advance_delivery_after_learning_evidence"
                if checkpoint_status == "verified"
                else "inspect_learning_evidence_and_reconcile"
                if checkpoint_status == "blocked"
                else "resume_same_learning_run_after_manifest_validation"
            ),
        }
        if checkpoint_status == "verified":
            payload["verified_head_sha"] = plan.source_revision
            payload["verification_evidence"] = [
                canonical_digest(item) for item in snapshot_copy.get("evidence", [])
            ]
        payload["checkpoint_digest"] = canonical_digest(payload)
        ledger.checkpoint(
            work_key, "harness_experiment", status=checkpoint_status,
            head_sha=plan.source_revision, payload=payload,
        )

    def probe(candidate, _task, manifest_payload, stage):
        if stage == "baseline" and stop_mode == "cancel":
            guard.cancel("integration cancellation during baseline probe")
        elif stage == "baseline" and stop_mode == "deadline":
            guard.tighten_deadline(0.001)
            time.sleep(0.01)
        return {
            "schema_version": "auren-isolation-receipt-v1",
            "manifest_digest": manifest_payload["manifest_digest"],
            "task_digest": manifest_payload["task_digest"],
            "harness_digest": canonical_digest(candidate),
            "isolation_profile": manifest_payload["isolation_profile"],
            "isolation_enforced": True,
            "egress_restricted": True,
            "secrets_absent": True,
            "workspace_digest": canonical_digest({"candidate": candidate, "stage": stage}),
            "stage": stage,
            "signer_id": "integration-test-operator",
            "signer_key_id": "integration-test-key",
            "test_signature_valid": True,
        }

    def verify_isolation(receipt, manifest_value, harness_digest):
        return (
            receipt.get("signer_id") == "integration-test-operator"
            and receipt.get("signer_key_id") == "integration-test-key"
            and receipt.get("test_signature_valid") is True
            and receipt.get("harness_digest") == harness_digest
            and receipt.get("manifest_digest") == manifest_value.digest
        )

    def judge(candidate, probe_receipt, manifest_payload):
        if stop_mode == "missing-evaluator":
            return {}
        mode = candidate.get("tool_guidance", {}).get("mode")
        return {
            "schema_version": "auren-evaluation-receipt-v1",
            "manifest_digest": manifest_payload["manifest_digest"],
            "task_digest": manifest_payload["task_digest"],
            "harness_digest": canonical_digest(candidate),
            "evaluator_id": manifest_payload["evaluator_id"],
            "independent_of_executor": True,
            "scored": True,
            "tests_passed": True,
            "security_passed": True,
            "reliability_passed": True,
            "quality_score": 0.9 if mode == "concise" else 0.8,
            "safety_score": 1.0,
            "reliability_score": 1.0,
            "cost": 1.0,
            "latency_ms": 100.0,
            "evidence_refs": [canonical_digest({"candidate": candidate, "probe": probe_receipt})],
        }

    controller = HarnessExperimentController(
        guard=guard, persist=persist, verify_isolation_receipt=verify_isolation,
        max_candidates=1, max_depth=0,
    )
    result = controller.run(
        manifest=manifest,
        active_harness=base,
        task=task,
        propose=lambda _base, _task, _depth, _seed: [
            {"tool_guidance": {"mode": "concise"}}
        ],
        probe=probe,
        judge=judge,
        adopt=lambda candidate, _expected: (
            adopted.append(canonical_digest(candidate))
            or {"atomic": True, "active_digest": canonical_digest(candidate)}
        ),
        rollback=lambda expected: {"restored": True, "active_digest": expected},
    )
    return manifest, result, adopted


def _start_integrated_plan(root, work_key):
    from pathlib import Path

    state_root = Path(root) / ".auren"
    state_root.mkdir(parents=True, exist_ok=True)
    plan = EngineeringPlan(
        run_id=work_key,
        goal="Integrate durable learning evidence with spec-driven delivery",
        requirements=("Learning and delivery must share a stable work identity.",),
        source_revision="a" * 40,
        budget={"dispatches": 4},
        tasks=(PlanTask(
            "learn-and-deliver", "Verify learning evidence and delivery state",
            ("Evidence-backed learning decision is durable",),
            "Restore the prior plan and harness digests",
        ),),
    )
    store = EngineeringPlanStore(state_root / "plans.sqlite3")
    ledger = EngineeringRecoveryLedger(state_root / "recovery.sqlite3")
    bridge = EngineeringPlanRecoveryBridge(store=store, ledger=ledger)
    bridge.start(plan)
    for status in ("ready", "running", "verifying"):
        bridge.transition_task(
            work_key, "learn-and-deliver", status,
            reason="integration test transition to " + status,
        )
    return plan, store, ledger, bridge


class ContinuousEngineeringIntegrationTests(unittest.TestCase):
    def test_learning_delivery_share_identity_and_reconcile_split_write_after_restart(self):
        import gc
        from portable.engineering_dashboard import EngineeringDashboard
        from portable.execution_controller import ExecutionController
        from portable.harness_experiments import canonical_digest
        from portable.runtime_recovery import RuntimeExecutionGuard

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_key = "AUREN:issue-696:continuous-engineering"
            plan, store, ledger, bridge = _start_integrated_plan(root, work_key)

            runtime = ExecutionController(root)
            runtime.register_handler(
                "continuous-engineering-integration",
                _continuous_engineering_integration_handler,
                execution_class="read_only",
            )
            first = runtime.submit(
                "continuous-engineering-integration", {"work_key": work_key},
                goal=plan.goal, work_key=work_key,
            )
            duplicate = runtime.submit(
                "continuous-engineering-integration", {"work_key": work_key},
                goal=plan.goal, work_key=work_key,
            )
            self.assertEqual(first.id, duplicate.id)
            import sqlite3
            db = sqlite3.connect(runtime.db_path)
            try:
                stored_key = db.execute("SELECT work_key FROM tasks WHERE id=?", (first.id,)).fetchone()
            finally:
                db.close()
            self.assertEqual(stored_key, (work_key,))
            task_id = first.id
            runtime.run_once(task_id)
            self.assertEqual(runtime.task(task_id).state, "completed")
            del runtime
            gc.collect()

            manifest, result, adopted = _run_integrated_harness_experiment(
                work_key=work_key, plan=plan, ledger=ledger,
                guard=RuntimeExecutionGuard(max_steps=20, max_attempts=20, deadline_seconds=60),
            )
            self.assertTrue(result.success)
            self.assertEqual(result.status, "adopted")
            self.assertEqual(adopted, [result.adopted_digest])

            learning_checkpoint = ledger.get_checkpoint(work_key, "harness_experiment")
            self.assertIsNotNone(learning_checkpoint)
            self.assertEqual(learning_checkpoint.status, "verified")
            self.assertEqual(learning_checkpoint.payload["work_key"], work_key)
            self.assertEqual(learning_checkpoint.payload["manifest_digest"], manifest.digest)
            self.assertEqual(learning_checkpoint.payload["plan_digest"], plan.digest)
            self.assertTrue(learning_checkpoint.payload["verification_evidence"])

            evidence_refs = (
                "learning://" + work_key + "/" + manifest.digest,
                "sha256:" + canonical_digest(learning_checkpoint.payload),
            )
            original_checkpoint = ledger.checkpoint

            def fail_plan_checkpoint(task_id, phase, **kwargs):
                if task_id == work_key and phase == bridge.PHASE:
                    raise OSError("injected plan-store/recovery-ledger split write")
                return original_checkpoint(task_id, phase, **kwargs)

            ledger.checkpoint = fail_plan_checkpoint
            try:
                with self.assertRaisesRegex(OSError, "split write"):
                    bridge.transition_task(
                        work_key, "learn-and-deliver", "passed",
                        reason="learning evidence and acceptance checks verified",
                        evidence_refs=evidence_refs,
                    )
            finally:
                ledger.checkpoint = original_checkpoint

            with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "run_digest"):
                bridge.load_verified(work_key)
            bridge.reconcile_checkpoint(
                work_key,
                reason="integration test inspected the persisted plan revision and learning receipt",
                expected_plan_digest=plan.digest,
            )

            restarted_ledger = EngineeringRecoveryLedger(root / ".auren" / "recovery.sqlite3")
            restarted = EngineeringPlanRecoveryBridge(
                store=EngineeringPlanStore(root / ".auren" / "plans.sqlite3"),
                ledger=restarted_ledger,
            )
            loaded_plan, loaded_run = restarted.load_verified(work_key)
            self.assertEqual(loaded_plan.digest, plan.digest)
            self.assertEqual(loaded_run.tasks[0].status, "passed")
            resumed_learning = restarted_ledger.get_checkpoint(work_key, "harness_experiment")
            self.assertIsNotNone(resumed_learning)
            self.assertEqual(resumed_learning.payload["manifest_digest"], manifest.digest)
            self.assertEqual(resumed_learning.status, "verified")

            restarted_runtime = ExecutionController(root)
            restarted_runtime.register_handler(
                "continuous-engineering-integration",
                _continuous_engineering_integration_handler,
                execution_class="read_only",
            )
            resumed_task = restarted_runtime.submit(
                "continuous-engineering-integration", {"work_key": work_key},
                goal=plan.goal, work_key=work_key,
            )
            self.assertEqual(resumed_task.id, task_id)
            self.assertEqual(restarted_runtime.task(task_id).state, "completed")

            dashboard = EngineeringDashboard(root)
            dashboard.record_event(
                run_id=work_key, event="stale-heartbeat", task_id="learn-and-deliver",
                status="running", detail="old event projection must not override durable plan",
            )
            snapshot = dashboard.snapshot().as_dict()
            plan_row = next(row for row in snapshot["plan_runs"] if row["run_id"] == work_key)
            checkpoint_rows = []
            for candidate in dashboard._sqlite_files():
                try:
                    with closing(sqlite3.connect(candidate)) as db, db:
                        exists = db.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_checkpoints'"
                        ).fetchone()
                        if exists:
                            rows = db.execute(
                                "SELECT status, head_sha, payload_json FROM recovery_checkpoints "
                                "WHERE task_id=? AND phase='engineering_plan'",
                                (work_key,),
                            ).fetchall()
                            checkpoint_rows.extend((str(candidate), row) for row in rows)
                except sqlite3.Error:
                    continue
            self.assertEqual(
                plan_row["status"], "completed",
                f"dashboard_row={plan_row!r}; checkpoint_rows={checkpoint_rows!r}; "
                f"plan_store={store.database_path.resolve()}",
            )
            self.assertEqual(plan_row["task_counts"], {"passed": 1})
            self.assertGreaterEqual(plan_row["evidence_count"], 2)
            self.assertTrue(all(row.get("run_id") != work_key for row in snapshot["executions"]))
            del restarted_runtime
            gc.collect()

    def test_cancel_deadline_and_missing_evaluator_cannot_complete_delivery(self):
        from portable.engineering_dashboard import EngineeringDashboard
        from portable.harness_experiments import canonical_digest
        from portable.runtime_recovery import RuntimeExecutionGuard

        for stop_mode in ("cancel", "deadline", "missing-evaluator"):
            with self.subTest(stop_mode=stop_mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                work_key = "AUREN:issue-696:continuous-engineering"
                plan, _store, ledger, bridge = _start_integrated_plan(root, work_key)
                manifest, result, adopted = _run_integrated_harness_experiment(
                    work_key=work_key, plan=plan, ledger=ledger,
                    guard=RuntimeExecutionGuard(max_steps=20, max_attempts=20, deadline_seconds=60),
                    stop_mode=stop_mode,
                )
                self.assertFalse(result.success)
                self.assertEqual(adopted, [])
                if stop_mode == "missing-evaluator":
                    self.assertEqual(result.status, "blocked")
                else:
                    self.assertEqual(result.status, "unscored")
                checkpoint = ledger.get_checkpoint(work_key, "harness_experiment")
                self.assertIsNotNone(checkpoint)
                self.assertEqual(checkpoint.status, "blocked")
                self.assertEqual(checkpoint.payload["manifest_digest"], manifest.digest)

                bridge.transition_task(
                    work_key, "learn-and-deliver", "blocked",
                    reason="learning gate did not produce verified evidence: " + stop_mode,
                    evidence_refs=("learning://" + work_key + "/" + manifest.digest,),
                )
                restarted = EngineeringPlanRecoveryBridge(
                    store=EngineeringPlanStore(root / ".auren" / "plans.sqlite3"),
                    ledger=EngineeringRecoveryLedger(root / ".auren" / "recovery.sqlite3"),
                )
                _, loaded_run = restarted.load_verified(work_key)
                self.assertEqual(loaded_run.tasks[0].status, "blocked")
                snapshot = EngineeringDashboard(root).snapshot().as_dict()
                plan_row = next(row for row in snapshot["plan_runs"] if row["run_id"] == work_key)
                self.assertEqual(plan_row["status"], "blocked")
                self.assertNotEqual(plan_row["status"], "completed")
                self.assertEqual(plan_row["task_counts"], {"blocked": 1})


    def test_cross_store_process_death_blocks_plan_projection_and_never_replays_delivery(self):
        import gc
        import sqlite3
        from portable.engineering_dashboard import EngineeringDashboard
        from portable.execution_controller import ExecutionController
        from portable.harness_experiments import canonical_digest
        from portable.runtime_recovery import RuntimeExecutionGuard

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_key = "AUREN:issue-696:continuous-engineering"
            plan, _store, ledger, _bridge = _start_integrated_plan(root, work_key)

            runtime = ExecutionController(root)
            runtime.register_handler(
                "continuous-engineering-integration",
                _continuous_engineering_integration_handler,
                execution_class="read_only",
            )
            task = runtime.submit(
                "continuous-engineering-integration", {"work_key": work_key},
                goal=plan.goal, work_key=work_key,
            )
            runtime.run_once(task.id)
            self.assertEqual(runtime.task(task.id).state, "completed")
            self.assertEqual(runtime.company._state(task.work_unit_id)[0], "completed")
            self.assertEqual(runtime.progress_journal.snapshot(task.id)["state"], "completed")
            with closing(sqlite3.connect(runtime.evidence_capture.path)) as db, db:
                completed_events = db.execute(
                    "SELECT COUNT(*) FROM engineering_events WHERE task_id=? AND event_type='task_completed'",
                    (task.id,),
                ).fetchone()[0]
            self.assertEqual(completed_events, 1)

            manifest, result, adopted = _run_integrated_harness_experiment(
                work_key=work_key, plan=plan, ledger=ledger,
                guard=RuntimeExecutionGuard(max_steps=20, max_attempts=20, deadline_seconds=60),
            )
            self.assertTrue(result.success)
            self.assertEqual(result.status, "adopted")
            self.assertEqual(adopted, [result.adopted_digest])
            learning = ledger.get_checkpoint(work_key, "harness_experiment")
            self.assertIsNotNone(learning)
            self.assertEqual(learning.status, "verified")
            self.assertEqual(learning.payload["manifest_digest"], manifest.digest)
            evidence_refs = (
                "learning://" + work_key + "/" + manifest.digest,
                "sha256:" + canonical_digest(learning.payload),
                "task://" + task.id,
                "receipt://" + task.work_unit_id,
                "evidence://task-terminal:" + task.id + ":completed",
            )
            del runtime
            gc.collect()

            process = multiprocessing.get_context("spawn").Process(
                target=_crash_after_integrated_plan_store_commit,
                args=(str(root), work_key, evidence_refs),
            )
            process.start()
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join(3)
                self.fail("cross-store crash fixture exceeded its time budget")
            self.assertEqual(91, process.exitcode)

            restarted_bridge = EngineeringPlanRecoveryBridge(
                store=EngineeringPlanStore(root / ".auren" / "plans.sqlite3"),
                ledger=EngineeringRecoveryLedger(root / ".auren" / "recovery.sqlite3"),
            )
            with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "run_digest"):
                restarted_bridge.load_verified(work_key)

            dashboard = EngineeringDashboard(root).snapshot().as_dict()
            plan_row = next(row for row in dashboard["plan_runs"] if row["run_id"] == work_key)
            self.assertEqual(plan_row["status"], "unknown")
            self.assertIn("reconciliation required", plan_row["detail"])
            progress_rows = dashboard["tasks"]["progress"]
            self.assertTrue(any(
                row.get("task_id") == task.id and row.get("state") == "completed"
                for row in progress_rows
            ))

            restarted_runtime = ExecutionController(root)
            restarted_runtime.register_handler(
                "continuous-engineering-integration",
                _continuous_engineering_integration_handler,
                execution_class="read_only",
            )
            duplicate = restarted_runtime.submit(
                "continuous-engineering-integration", {"work_key": work_key},
                goal=plan.goal, work_key=work_key,
            )
            self.assertEqual(duplicate.id, task.id)
            self.assertEqual(restarted_runtime.task(task.id).state, "completed")
            self.assertEqual(restarted_runtime.task(task.id).attempts, 1)
            self.assertIsNone(restarted_runtime.run_once(task.id))
            self.assertEqual(restarted_runtime.task(task.id).attempts, 1)
            self.assertEqual(restarted_runtime.progress_journal.snapshot(task.id)["state"], "completed")
            self.assertEqual(restarted_runtime.company._state(task.work_unit_id)[0], "completed")
            with closing(sqlite3.connect(restarted_runtime.evidence_capture.path)) as db, db:
                completed_events = db.execute(
                    "SELECT COUNT(*) FROM engineering_events WHERE task_id=? AND event_type='task_completed'",
                    (task.id,),
                ).fetchone()[0]
            self.assertEqual(completed_events, 1)
            learning_after_restart = EngineeringRecoveryLedger(
                root / ".auren" / "recovery.sqlite3"
            ).get_checkpoint(work_key, "harness_experiment")
            self.assertIsNotNone(learning_after_restart)
            self.assertEqual(learning_after_restart.status, "verified")
            self.assertEqual(learning_after_restart.payload["manifest_digest"], manifest.digest)

            restarted_bridge.reconcile_checkpoint(
                work_key,
                reason="inspected task row, work-unit receipt, progress journal, evidence index, and learning checkpoint after process death",
                expected_plan_digest=plan.digest,
            )
            loaded_plan, loaded_run = restarted_bridge.load_verified(work_key)
            self.assertEqual(loaded_plan.digest, plan.digest)
            self.assertEqual(loaded_run.tasks[0].status, "passed")
            final_dashboard = EngineeringDashboard(root).snapshot().as_dict()
            final_row = next(row for row in final_dashboard["plan_runs"] if row["run_id"] == work_key)
            self.assertEqual(final_row["status"], "completed")


    def test_integrated_terminal_receipt_crash_repairs_evidence_without_replay(self):
        import gc
        import sqlite3
        from portable.engineering_dashboard import EngineeringDashboard
        from portable.execution_controller import ExecutionController
        from portable.harness_experiments import canonical_digest
        from portable.runtime_recovery import RuntimeExecutionGuard

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_key = "AUREN:issue-696:continuous-engineering"
            plan, _store, ledger, bridge = _start_integrated_plan(root, work_key)
            manifest, result, adopted = _run_integrated_harness_experiment(
                work_key=work_key,
                plan=plan,
                ledger=ledger,
                guard=RuntimeExecutionGuard(max_steps=20, max_attempts=20, deadline_seconds=60),
            )
            self.assertTrue(result.success)
            self.assertEqual(result.status, "adopted")
            self.assertEqual(adopted, [result.adopted_digest])
            learning = ledger.get_checkpoint(work_key, "harness_experiment")
            self.assertIsNotNone(learning)
            self.assertEqual(learning.status, "verified")
            self.assertEqual(learning.payload["manifest_digest"], manifest.digest)

            payload = {
                "work_key": work_key,
                "marker_path": str(root / "handler-calls.log"),
            }
            runtime = ExecutionController(root)
            runtime.register_handler(
                "continuous-engineering-marker",
                _continuous_engineering_marker_handler,
                execution_class="read_only",
            )
            task = runtime.submit(
                "continuous-engineering-marker", payload,
                goal=plan.goal, work_key=work_key,
            )
            evidence_path = runtime.evidence_capture.path
            del runtime
            gc.collect()

            process = multiprocessing.get_context("spawn").Process(
                target=_crash_after_integrated_terminal_receipt_before_evidence,
                args=(str(root), work_key, plan.goal),
            )
            process.start()
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join(3)
                self.fail("integrated terminal-receipt crash fixture exceeded its time budget")
            self.assertEqual(93, process.exitcode)
            marker = root / "handler-calls.log"
            self.assertEqual(["called"], marker.read_text(encoding="utf-8").splitlines())

            with closing(sqlite3.connect(evidence_path)) as db, db:
                self.assertEqual(
                    0,
                    db.execute(
                        "SELECT COUNT(*) FROM engineering_events WHERE task_id=? "
                        "AND event_type='task_completed'",
                        (task.id,),
                    ).fetchone()[0],
                )

            restarted = ExecutionController(root)
            restarted.register_handler(
                "continuous-engineering-marker",
                _continuous_engineering_marker_handler,
                execution_class="read_only",
            )
            self.assertEqual(restarted.task(task.id).state, "completed")
            self.assertEqual(restarted.task(task.id).attempts, 1)
            self.assertEqual(restarted.company._state(task.work_unit_id)[0], "completed")
            self.assertEqual(restarted.progress_journal.snapshot(task.id)["state"], "completed")
            self.assertIsNone(restarted.run_once(task.id))
            self.assertEqual(["called"], marker.read_text(encoding="utf-8").splitlines())

            with closing(sqlite3.connect(restarted.evidence_capture.path)) as db, db:
                events = db.execute(
                    "SELECT event_id, work_unit_id, status FROM engineering_events "
                    "WHERE task_id=? AND event_type='task_completed'",
                    (task.id,),
                ).fetchall()
            self.assertEqual(
                [(f"task-terminal:{task.id}:completed", task.work_unit_id, "completed")],
                events,
            )
            self.assertEqual(0, restarted.reconcile_terminal_events()["repaired"])
            self.assertFalse(any(
                "terminal_evidence_reconciliation_required" in finding
                for finding in restarted.health_findings()
            ))

            evidence_refs = (
                "learning://" + work_key + "/" + manifest.digest,
                "sha256:" + canonical_digest(learning.payload),
                "task://" + task.id,
                "receipt://" + task.work_unit_id,
                "evidence://task-terminal:" + task.id + ":completed",
            )
            bridge.transition_task(
                work_key, "learn-and-deliver", "passed",
                reason=(
                    "terminal receipt, repaired evidence index, and "
                    "verified learning checkpoint agree"
                ),
                evidence_refs=evidence_refs,
            )
            resumed_bridge = EngineeringPlanRecoveryBridge(
                store=EngineeringPlanStore(root / ".auren" / "plans.sqlite3"),
                ledger=EngineeringRecoveryLedger(root / ".auren" / "recovery.sqlite3"),
            )
            loaded_plan, loaded_run = resumed_bridge.load_verified(work_key)
            self.assertEqual(plan.digest, loaded_plan.digest)
            self.assertEqual("passed", loaded_run.tasks[0].status)
            self.assertEqual("verified", resumed_bridge.ledger.get_checkpoint(
                work_key, "harness_experiment"
            ).status)

            snapshot = EngineeringDashboard(root).snapshot().as_dict()
            plan_row = next(row for row in snapshot["plan_runs"] if row["run_id"] == work_key)
            self.assertEqual("completed", plan_row["status"])
            self.assertEqual({"passed": 1}, plan_row["task_counts"])
            self.assertGreaterEqual(plan_row["evidence_count"], 2)
            del restarted
            gc.collect()



    def test_integrated_task_row_receipt_split_blocks_plan_dashboard_and_never_replays(self):
        import gc
        import sqlite3
        from portable.engineering_dashboard import EngineeringDashboard
        from portable.execution_controller import ExecutionController
        from portable.runtime_recovery import RuntimeExecutionGuard

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_key = "AUREN:issue-696:continuous-engineering"
            plan, _store, ledger, bridge = _start_integrated_plan(root, work_key)
            manifest, result, adopted = _run_integrated_harness_experiment(
                work_key=work_key,
                plan=plan,
                ledger=ledger,
                guard=RuntimeExecutionGuard(max_steps=20, max_attempts=20, deadline_seconds=60),
            )
            self.assertTrue(result.success)
            self.assertEqual("adopted", result.status)
            self.assertEqual([result.adopted_digest], adopted)
            learning = ledger.get_checkpoint(work_key, "harness_experiment")
            self.assertIsNotNone(learning)
            self.assertEqual("verified", learning.status)
            self.assertEqual(manifest.digest, learning.payload["manifest_digest"])

            runtime = ExecutionController(root)
            runtime.register_handler(
                "continuous-engineering-marker",
                _continuous_engineering_marker_handler,
                execution_class="read_only",
            )
            process = multiprocessing.get_context("spawn").Process(
                target=_crash_after_integrated_task_terminal_before_work_unit_receipt,
                args=(str(root), work_key, plan.goal),
            )
            process.start()
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join(3)
                self.fail("integrated task/receipt crash fixture exceeded its time budget")
            self.assertEqual(95, process.exitcode)
            marker = root / "handler-calls.log"
            self.assertEqual(["called"], marker.read_text(encoding="utf-8").splitlines())

            with closing(sqlite3.connect(runtime.db_path)) as db:
                task_rows = db.execute(
                    "SELECT id,state,attempts,work_unit_id FROM tasks WHERE work_key=?",
                    (work_key,),
                ).fetchall()
            self.assertEqual(1, len(task_rows))
            task_id, task_state, attempts, work_unit_id = task_rows[0]
            self.assertEqual("completed", task_state)
            self.assertEqual(1, attempts)
            self.assertEqual("running", runtime.company._state(work_unit_id)[0])
            with closing(sqlite3.connect(runtime.evidence_capture.path)) as db:
                self.assertEqual(
                    0,
                    db.execute(
                        "SELECT COUNT(*) FROM engineering_events WHERE task_id=? "
                        "AND event_type IN ('task_completed','task_failed')",
                        (task_id,),
                    ).fetchone()[0],
                )
            del runtime
            gc.collect()

            restarted = ExecutionController(root)
            restarted.register_handler(
                "continuous-engineering-marker",
                _continuous_engineering_marker_handler,
                execution_class="read_only",
            )
            self.assertEqual("blocked", restarted.task(task_id).state)
            self.assertEqual(1, restarted.task(task_id).attempts)
            duplicate = restarted.submit(
                "continuous-engineering-marker",
                {"work_key": work_key, "marker_path": str(marker)},
                goal=plan.goal,
                work_key=work_key,
            )
            self.assertEqual(task_id, duplicate.id)
            self.assertEqual(work_unit_id, duplicate.work_unit_id)
            self.assertIsNone(restarted.run_once(task_id))
            self.assertEqual(["called"], marker.read_text(encoding="utf-8").splitlines())
            self.assertEqual("running", restarted.company._state(work_unit_id)[0])
            with restarted.company._db() as db:
                self.assertEqual(
                    1,
                    db.execute(
                        "SELECT COUNT(*) FROM work_units WHERE work_key=?", (work_key,),
                    ).fetchone()[0],
                )
            self.assertTrue(any(
                "progress_reconciliation_required" in finding
                for finding in restarted.health_findings()
            ))

            bridge.transition_task(
                work_key,
                "learn-and-deliver",
                "blocked",
                reason=(
                    "task row is terminal but the work-unit receipt is still running; "
                    "keep delivery blocked until explicit receipt reconciliation"
                ),
                next_safe_action="inspect_task_receipt_split_and_reconcile",
            )
            loaded_plan, loaded_run = bridge.load_verified(work_key)
            self.assertEqual(plan.digest, loaded_plan.digest)
            self.assertEqual("blocked", loaded_run.tasks[0].status)
            phase_checkpoint = ledger.get_checkpoint(work_key, bridge.PHASE)
            self.assertEqual("in_progress", phase_checkpoint.status)
            self.assertEqual(
                "inspect_task_receipt_split_and_reconcile",
                phase_checkpoint.payload["next_safe_action"],
            )
            self.assertEqual("verified", ledger.get_checkpoint(work_key, "harness_experiment").status)

            dashboard = EngineeringDashboard(root).snapshot().as_dict()
            plan_row = next(row for row in dashboard["plan_runs"] if row["run_id"] == work_key)
            self.assertEqual("blocked", plan_row["status"])
            self.assertEqual({"blocked": 1}, plan_row["task_counts"])
            self.assertNotEqual("completed", plan_row["status"])
            self.assertIsNone(restarted.run_once(task_id))
            with closing(sqlite3.connect(restarted.evidence_capture.path)) as db:
                self.assertEqual(
                    0,
                    db.execute(
                        "SELECT COUNT(*) FROM engineering_events WHERE task_id=? "
                        "AND event_type IN ('task_completed','task_failed')",
                        (task_id,),
                    ).fetchone()[0],
                )



if __name__ == "__main__":
    unittest.main()
