from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable.action_governance import ActionRequest
from portable.durable_approval import DurableApprovalStore
from portable.engineering_plan_dispatch import EngineeringPlanDispatch
from portable.execution_controller import ExecutionController
from portable.engineering_plan_recovery import EngineeringPlanCheckpointMismatch
from portable.runtime_recovery import ExecutionBudgetExceeded, RuntimeExecutionGuard
from portable.trusted_plan_dispatch import TrustedPlanDispatchRoot
from tests.test_engineering_plan_dispatch import prepared


class TrustedPlanDispatchRootTests(unittest.TestCase):
    def _approval(self, controller, *, context, plan_task_id="implement", expiry_seconds=3600, clock=None):
        approval_now = clock() if clock is not None else datetime.now(timezone.utc)
        store = DurableApprovalStore(
            controller,
            approver_authorizer=lambda approver, role, scope, roles: (
                approver == "manager-1" and role in roles and scope == "repo:write"
            ),
            context_provider=lambda request: dict(context),
            clock=clock,
        )
        request = ActionRequest(
            run_id="approval-run", action_id="publish-change", actor="agent:implementer",
            capability="publish", action="publish", target="repo:main",
            risk="external_effect", idempotency_key="publish:trusted-root",
            evidence_refs=("sha256:" + "d" * 64,), task_id="approval-task",
        )
        record = store.create_request(
            request=request, operation_id="publish:trusted-root",
            artifact_digest=context["artifact_digest"],
            evidence_digest=context["evidence_digest"],
            policy_digest=context["policy_digest"],
            requester_id="worker-1", required_role="engineering_manager",
            scope="repo:write",
            checkpoint={
                "plan_run_id": self.plan.run_id,
                "plan_task_id": plan_task_id,
                "plan_digest": self.plan.digest,
                "run_revision": self.packet.run_revision,
                "source_revision": self.packet.source_revision,
            },
            expires_at=approval_now + timedelta(seconds=expiry_seconds),
        )
        store.decide(
            record.approval_id, "approve", approver_id="manager-1",
            roles=("engineering_manager",), scope="repo:write",
        )
        return store, request, record

    def _root(self, controller, store, request, record, guard, *, context):
        admission_checkpoint_digest = guard.snapshot()["digest"]
        return TrustedPlanDispatchRoot(
            controller=controller,
            store=self.plan_store,
            recovery_bridge=self.recovery_bridge,
            approval_store=store,
            budget_resolver=lambda run_id, task_id, handler: {
                "budget_id": "crew:team-a:run-1", "guard": guard,
                "checkpoint_digest": admission_checkpoint_digest, "policy_digest": "a" * 64,
            },
            approval_resolver=lambda run_id, task_id, handler: {
                "approval_id": record.approval_id, "request": request,
            },
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        plan, run, packet, _, store, controller, bridge = prepared(self.temp.name)
        self.plan = plan
        self.packet = packet
        self.plan_store = store
        self.controller = controller
        self.recovery_bridge = bridge.recovery_bridge
        self.context = {
            "artifact_digest": "1" * 64,
            "evidence_digest": "2" * 64,
            "policy_digest": "3" * 64,
        }

    def test_root_resolves_live_budget_and_approved_record_and_binds_them(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        task = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="trusted-dispatch-1",
            handler="dispatch-test", packet=self.packet, payload={"change": "small"},
        )
        binding = task.payload["auren_dispatch"]
        self.assertEqual("crew:team-a:run-1", binding["shared_budget_binding"]["budget_id"])
        self.assertEqual(guard.snapshot()["digest"], binding["shared_budget_binding"]["checkpoint_digest"])
        self.assertEqual("a" * 64, binding["shared_budget_binding"]["policy_digest"])
        self.assertEqual(record.approval_id, binding["approval_evidence"]["approval_id"])
        self.assertEqual(record.evidence_digest, binding["approval_evidence"]["evidence_digest"])
        self.assertEqual("approved", binding["approval_evidence"]["decision"])
        self.assertEqual("manager-1", binding["approval_evidence"]["approver_id"])
        self.assertTrue(approvals.verify_audit_chain())

    def test_legacy_controller_dispatch_is_fenced_after_root_bootstrap(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        self._root(self.controller, approvals, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "trusted plan dispatch composition root"):
            self.controller.dispatch_engineering_plan_task(
                store=self.plan_store, run_id=self.plan.run_id, task_id="implement",
                dispatch_key="untrusted-bypass", handler="dispatch-test",
                packet=self.packet, recovery_bridge=self.recovery_bridge,
                shared_budget_binding={
                    "budget_id": "fake", "checkpoint_digest": "f" * 64, "policy_digest": "e" * 64,
                },
                approval_evidence={
                    "approval_id": "fake", "receipt_digest": "f" * 64,
                    "evidence_digest": "f" * 64, "policy_digest": "f" * 64,
                    "approver_id": "worker", "approver_role": "ceo", "decision": "approved",
                },
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_pending_approval_and_changed_context_fail_before_dispatch(self):
        store = DurableApprovalStore(
            self.controller,
            approver_authorizer=lambda *args: True,
            context_provider=lambda request: dict(self.context),
        )
        request = ActionRequest(
            run_id="approval-run", action_id="publish-change", actor="agent:implementer",
            capability="publish", action="publish", target="repo:main",
            risk="external_effect", idempotency_key="publish:pending",
            evidence_refs=("sha256:" + "d" * 64,), task_id="approval-task",
        )
        record = store.create_request(
            request=request, operation_id="publish:pending",
            artifact_digest=self.context["artifact_digest"],
            evidence_digest=self.context["evidence_digest"],
            policy_digest=self.context["policy_digest"],
            requester_id="worker-1", required_role="engineering_manager",
            scope="repo:write", checkpoint={
                "plan_run_id": self.plan.run_id,
                "plan_task_id": "implement",
                "plan_digest": self.plan.digest,
                "run_revision": self.packet.run_revision,
                "source_revision": self.packet.source_revision,
            }, expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = TrustedPlanDispatchRoot(
            controller=self.controller, store=self.plan_store,
            recovery_bridge=self.recovery_bridge, approval_store=store,
            budget_resolver=lambda run_id, task_id, handler: {
                "budget_id": "crew:team-a:run-1", "guard": guard,
                "checkpoint_digest": guard.snapshot()["digest"], "policy_digest": "a" * 64,
            },
            approval_resolver=lambda run_id, task_id, handler: {"approval_id": record.approval_id, "request": request},
        )
        with self.assertRaisesRegex(PermissionError, "not approved"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="pending-approval",
                handler="dispatch-test", packet=self.packet,
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_cancelled_canonical_guard_blocks_dispatch(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        guard.cancel("operator cancelled")
        with self.assertRaises(ExecutionBudgetExceeded):
            TrustedPlanDispatchRoot(
                controller=self.controller, store=self.plan_store,
                recovery_bridge=self.recovery_bridge, approval_store=approvals,
                budget_resolver=lambda run_id, task_id, handler: {
                    "budget_id": "crew:team-a:run-1", "guard": guard,
                    "checkpoint_digest": guard.snapshot()["digest"], "policy_digest": "a" * 64,
                },
                approval_resolver=lambda run_id, task_id, handler: {
                    "approval_id": record.approval_id, "request": request,
                },
            ).dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="cancelled-budget",
                handler="dispatch-test", packet=self.packet,
            )

    def test_direct_dispatch_cannot_supply_forged_receipt_after_root_registration(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        self._root(self.controller, approvals, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "trusted plan dispatch composition root"):
            EngineeringPlanDispatch(
                store=self.plan_store, controller=self.controller,
                recovery_bridge=self.recovery_bridge,
            ).dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="direct-forged",
                handler="dispatch-test", packet=self.packet,
                shared_budget_binding={
                    "budget_id": "fake", "checkpoint_digest": "f" * 64, "policy_digest": "e" * 64,
                },
                approval_evidence={
                    "approval_id": "fake", "receipt_digest": "f" * 64,
                    "evidence_digest": "f" * 64, "policy_digest": "f" * 64,
                    "approver_id": "worker", "approver_role": "ceo", "decision": "approved",
                },
            )


    def test_adaptive_runtime_bootstrap_installs_the_trusted_dispatch_root(self):
        from portable.adaptive_runtime import AdaptiveRuntime
        from portable.engineering_plan_recovery import EngineeringPlanRecoveryBridge
        from portable.engineering_plan_store import EngineeringPlanStore
        from portable.engineering_recovery import EngineeringRecoveryLedger
        from portable.orchestration import Graph

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runtime = AdaptiveRuntime(Graph([]), state_root=Path(tmp) / "runtime")
            store = EngineeringPlanStore(Path(tmp) / "plans.sqlite3")
            bridge = EngineeringPlanRecoveryBridge(
                store=store,
                ledger=EngineeringRecoveryLedger(Path(tmp) / "runtime" / "recovery.sqlite3"),
            )
            approvals = DurableApprovalStore(runtime.execution_controller)
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                runtime.dispatch_engineering_plan_task(
                    run_id="run", task_id="task", dispatch_key="key",
                    handler="dispatch-test", packet=self.packet,
                )
            with self.assertRaisesRegex(PermissionError, "trusted plan dispatch composition root"):
                runtime.execution_controller.dispatch_engineering_plan_task(
                    store=self.plan_store, run_id=self.plan.run_id, task_id="implement",
                    dispatch_key="direct-before-bootstrap", handler="dispatch-test",
                    packet=self.packet, recovery_bridge=self.recovery_bridge,
                )
            root = runtime.configure_trusted_plan_dispatch(
                store=store,
                recovery_bridge=bridge,
                approval_store=approvals,
                budget_resolver=lambda run_id, task_id, handler: {},
                approval_resolver=lambda run_id, task_id, handler: None,
            )
            self.assertIs(runtime.trusted_plan_dispatch_root, root)
            self.assertIsNotNone(runtime.execution_controller._trusted_plan_dispatch_token)



    def test_approval_for_another_plan_task_cannot_be_reused(self):
        approvals, request, record = self._approval(
            self.controller, context=self.context, plan_task_id="review",
        )
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "exact plan task revision"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="wrong-task-approval",
                handler="dispatch-test", packet=self.packet,
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])



    def test_approved_but_expired_record_is_rejected(self):
        approvals, request, pending_record = self._approval(
            self.controller, context=self.context,
        )
        record = approvals.get(pending_record.approval_id)
        self.assertEqual("approved", record.status)

        approvals.clock = lambda: datetime.now(timezone.utc) + timedelta(hours=2)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "expired"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="expired-approval",
                handler="dispatch-test", packet=self.packet,
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])


    def test_caller_deadline_cannot_extend_supervisor_budget(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4, deadline_seconds=60)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        task = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="bounded-supervisor-deadline",
            handler="dispatch-test", packet=self.packet, deadline_seconds=300,
        )
        with sqlite3.connect(self.controller.db_path) as db:
            deadline_seconds = db.execute(
                "SELECT deadline_seconds FROM tasks WHERE id=?", (task.id,),
            ).fetchone()[0]
        self.assertGreater(deadline_seconds, 0)
        self.assertLessEqual(deadline_seconds, 60.0)


    def test_worker_resume_requires_trusted_root_bootstrap(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        task = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="resume-root-required",
            handler="dispatch-test", packet=self.packet, payload={"change": "small"},
        )
        token = self.controller._trusted_plan_dispatch_token
        # Simulate a fresh low-level controller: no in-memory root-required flag
        # survives, but the persisted trusted binding still requires bootstrap.
        self.controller._trusted_plan_dispatch_required = False
        self.controller._trusted_plan_dispatch_token = None
        with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "root is not configured"):
            self.controller._verify_engineering_plan_dispatch_checkpoint(task)
        self.controller._trusted_plan_dispatch_token = token
        self.controller._verify_engineering_plan_dispatch_checkpoint(task)


    def test_trusted_binding_is_revalidated_at_controller_preflight(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        task = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="trusted-preflight",
            handler="dispatch-test", packet=self.packet, payload={"change": "small"},
        )

        # The real controller preflight must reproduce the root's full binding,
        # including the durable budget and approval receipts.
        self.controller._verify_engineering_plan_dispatch_checkpoint(task)

        tampered_payload = dict(task.payload)
        tampered_metadata = dict(tampered_payload["auren_dispatch"])
        tampered_budget = dict(tampered_metadata["shared_budget_binding"])
        tampered_budget["policy_digest"] = "b" * 64
        tampered_metadata["shared_budget_binding"] = tampered_budget
        tampered_payload["auren_dispatch"] = tampered_metadata
        with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "binding"):
            self.controller._verify_engineering_plan_dispatch_checkpoint(
                replace(task, payload=tampered_payload)
            )


    def test_approved_record_with_changed_current_artifact_context_is_rejected(self):
        approvals, request, pending_record = self._approval(
            self.controller, context=self.context,
        )
        record = approvals.get(pending_record.approval_id)
        self.assertEqual("approved", record.status)
        self.context["artifact_digest"] = "9" * 64
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "artifact_digest"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="changed-approval-context",
                handler="dispatch-test", packet=self.packet,
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])



    def test_consequential_handler_without_canonical_approval_is_blocked(self):
        # Exercise the root's consequential gate without registering an
        # operator-owned deployment handler or inventing a promotion policy.
        self.controller._handler_execution_class = lambda handler: "consequential"
        approvals = DurableApprovalStore(self.controller, context_provider=lambda request: dict(self.context))
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = TrustedPlanDispatchRoot(
            controller=self.controller, store=self.plan_store,
            recovery_bridge=self.recovery_bridge, approval_store=approvals,
            budget_resolver=lambda run_id, task_id, handler: {
                "budget_id": "crew:team-a:run-1", "guard": guard,
                "checkpoint_digest": guard.snapshot()["digest"], "policy_digest": "a" * 64,
            },
            approval_resolver=lambda run_id, task_id, handler: None,
        )
        with self.assertRaisesRegex(PermissionError, "requires a canonical durable approval"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="missing-approval",
                handler="dispatch-test", packet=self.packet,
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])



    def test_replay_uses_immutable_admission_digest_after_shared_budget_is_charged(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        first = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="stable-budget-replay",
            handler="dispatch-test", packet=self.packet,
        )
        admission_digest = first.payload["auren_dispatch"]["shared_budget_binding"]["checkpoint_digest"]
        self.assertTrue(guard.charge_attempt("specialist:implementation"))
        replay = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="stable-budget-replay",
            handler="dispatch-test", packet=self.packet,
        )
        self.assertEqual(first.id, replay.id)
        self.assertEqual(
            admission_digest, replay.payload["auren_dispatch"]["shared_budget_binding"]["checkpoint_digest"],
        )
        self.assertEqual(1, guard.attempts_used)



    def test_approval_expiry_row_cannot_be_extended_outside_the_audit_chain(self):
        approvals, request, pending_record = self._approval(
            self.controller, context=self.context,
        )
        record = approvals.get(pending_record.approval_id)
        with sqlite3.connect(self.controller.db_path) as db:
            db.execute(
                "UPDATE durable_approvals SET expires_at=? WHERE approval_id=?",
                ((datetime.now(timezone.utc) + timedelta(days=30)).isoformat(), record.approval_id),
            )
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "immutable request event"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="tampered-expiry",
                handler="dispatch-test", packet=self.packet,
            )

    def test_approval_state_row_cannot_diverge_from_audit_chain(self):
        approvals, request, pending_record = self._approval(
            self.controller, context=self.context,
        )
        record = approvals.get(pending_record.approval_id)
        with sqlite3.connect(self.controller.db_path) as db:
            db.execute(
                "UPDATE durable_approvals SET version=version+1 WHERE approval_id=?",
                (record.approval_id,),
            )
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "append-only audit chain"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="tampered-state",
                handler="dispatch-test", packet=self.packet,
            )



    def test_supervisor_cancellation_during_approval_resolution_blocks_queue_insert(self):
        approvals, request, pending_record = self._approval(
            self.controller, context=self.context,
        )
        record = approvals.get(pending_record.approval_id)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        admission_digest = guard.snapshot()["digest"]

        def resolve_approval(run_id, task_id, handler):
            guard.cancel("cancelled while approval context was resolving")
            return {"approval_id": record.approval_id, "request": request}

        root = TrustedPlanDispatchRoot(
            controller=self.controller, store=self.plan_store,
            recovery_bridge=self.recovery_bridge, approval_store=approvals,
            budget_resolver=lambda run_id, task_id, handler: {
                "budget_id": "crew:team-a:run-1", "guard": guard,
                "checkpoint_digest": admission_digest, "policy_digest": "a" * 64,
            },
            approval_resolver=resolve_approval,
        )
        with self.assertRaises(ExecutionBudgetExceeded):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="cancel-during-approval",
                handler="dispatch-test", packet=self.packet,
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])



if __name__ == "__main__":
    unittest.main()
