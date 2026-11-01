from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable.action_governance import ActionRequest
from portable.durable_approval import DurableApprovalStore
from portable.engineering_plan_dispatch import EngineeringPlanDispatch
from portable.execution_controller import ExecutionController
from portable.runtime_recovery import ExecutionBudgetExceeded, RuntimeExecutionGuard
from portable.trusted_plan_dispatch import TrustedPlanDispatchRoot
from tests.test_engineering_plan_dispatch import prepared


class TrustedPlanDispatchRootTests(unittest.TestCase):
    def _approval(self, controller, *, context):
        store = DurableApprovalStore(
            controller,
            approver_authorizer=lambda approver, role, scope, roles: (
                approver == "manager-1" and role in roles and scope == "repo:write"
            ),
            context_provider=lambda request: dict(context),
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
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        store.decide(
            record.approval_id, "approve", approver_id="manager-1",
            roles=("engineering_manager",), scope="repo:write",
        )
        return store, request, record

    def _root(self, controller, store, request, record, guard, *, context):
        return TrustedPlanDispatchRoot(
            controller=controller,
            store=self.plan_store,
            recovery_bridge=self.recovery_bridge,
            approval_store=store,
            budget_resolver=lambda run_id, task_id, handler: {
                "budget_id": "crew:team-a:run-1", "guard": guard,
                "policy_digest": "a" * 64,
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
            scope="repo:write", expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = TrustedPlanDispatchRoot(
            controller=self.controller, store=self.plan_store,
            recovery_bridge=self.recovery_bridge, approval_store=store,
            budget_resolver=lambda run_id, task_id, handler: {
                "budget_id": "crew:team-a:run-1", "guard": guard, "policy_digest": "a" * 64,
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
                    "budget_id": "crew:team-a:run-1", "guard": guard, "policy_digest": "a" * 64,
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


if __name__ == "__main__":
    unittest.main()
