from __future__ import annotations

from datetime import datetime, timedelta, timezone
import tempfile
import unittest

from portable.action_governance import ActionRequest
from portable.agent_org_runtime import authorize_agent_assignment
from portable.durable_approval import DurableApprovalStore
from portable.engineering_plan_dispatch import EngineeringPlanDispatch
from portable.engineering_plan_recovery import EngineeringPlanCheckpointMismatch
from portable.execution_controller import ExecutionController
from portable.runtime_recovery import RuntimeExecutionGuard
from portable.trusted_plan_dispatch import TrustedPlanDispatchRoot
from tests.test_engineering_plan_dispatch import DISPATCH_HANDLER_CALLS, prepared


class TrustedPlanApprovalPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        plan, _run, packet, _bridge, store, controller, bridge = prepared(self.temp.name)
        self.plan = plan
        self.packet = packet
        self.plan_store = store
        self.controller = controller
        self.recovery_bridge = bridge.recovery_bridge
        self.controller.register_handler(
            "dispatch-test",
            "tests.test_engineering_plan_dispatch:dispatch_test_handler",
            execution_class="read_only",
            agent_action_class="edit_assigned_paths",
        )
        self.assignment_scope = ("tests",)
        self.assignment_policy_path = self.controller.root / ".auren" / "guard_policy.json"
        self.assignment_policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.assignment_policy_path.write_text(
            '{"schema_version":"auren-guard-v1","default_effect":"allow","rules":[]}',
            encoding="utf-8",
        )
        self.context = {
            "artifact_digest": "1" * 64,
            "evidence_digest": "2" * 64,
            "policy_digest": "3" * 64,
        }

    def _approval(self, controller):
        now = datetime.now(timezone.utc)
        approvals = DurableApprovalStore(
            controller,
            approver_authorizer=lambda approver, role, scope, roles: (
                approver == "manager-1" and role in roles and scope == "repo:write"
            ),
            context_provider=lambda _request: dict(self.context),
        )
        request = ActionRequest(
            run_id="approval-run",
            action_id="publish-change",
            actor="agent:implementer",
            capability="publish",
            action="publish",
            target="repo:main",
            risk="external_effect",
            idempotency_key="publish:worker-preflight",
            evidence_refs=("sha256:" + "d" * 64,),
            task_id="approval-task",
        )
        record = approvals.create_request(
            request=request,
            operation_id="publish:worker-preflight",
            artifact_digest=self.context["artifact_digest"],
            evidence_digest=self.context["evidence_digest"],
            policy_digest=self.context["policy_digest"],
            requester_id="worker-1",
            required_role="engineering_manager",
            scope="repo:write",
            checkpoint={
                "plan_run_id": self.plan.run_id,
                "plan_task_id": "implement",
                "plan_digest": self.plan.digest,
                "run_revision": self.packet.run_revision,
                "source_revision": self.packet.source_revision,
            },
            expires_at=now + timedelta(hours=1),
        )
        approvals.decide(
            record.approval_id,
            "approve",
            approver_id="manager-1",
            roles=("engineering_manager",),
            scope="repo:write",
        )
        return approvals, request, record

    def _assignment_resolver(self, context):
        return authorize_agent_assignment(
            role_id="BE-01",
            manager_id="EM-ENG-01",
            action_class="edit_assigned_paths",
            scope=self.assignment_scope,
            authorizer=lambda request: request.get("scope") == self.assignment_scope,
            policy_path=self.assignment_policy_path,
            work_key=context["dispatch_key"],
            task_payload=context["payload"],
            deadline_seconds=context.get(
                "assignment_deadline_seconds", context["deadline_seconds"]
            ),
        )

    def _root(self, controller, approvals, request, record, guard, checkpoint_digest):
        return TrustedPlanDispatchRoot(
            controller=controller,
            store=self.plan_store,
            recovery_bridge=self.recovery_bridge,
            approval_store=approvals,
            budget_resolver=lambda _run, _task, _handler: {
                "budget_id": "crew:team-a:run-1",
                "guard": guard,
                "checkpoint_digest": checkpoint_digest,
                "policy_digest": "a" * 64,
            },
            approval_resolver=lambda _run, _task, _handler: {
                "approval_id": record.approval_id,
                "request": request,
            },
            assignment_resolver=self._assignment_resolver,
        )

    def test_approver_revocation_after_enqueue_blocks_fresh_process_preflight(self):
        approvals, request, record = self._approval(self.controller)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        checkpoint_digest = guard.snapshot()["digest"]
        root = self._root(
            self.controller, approvals, request, record, guard, checkpoint_digest,
        )
        DISPATCH_HANDLER_CALLS.clear()
        task = root.dispatch(
            run_id=self.plan.run_id,
            task_id="implement",
            dispatch_key="approval-revocation-after-enqueue",
            handler="dispatch-test",
            packet=self.packet,
            payload={"resource": "tests"},
        )
        self.assertEqual("approved", approvals.get(record.approval_id).status)

        # Rebuild the controller and approval store as if the worker restarted.
        # The fresh host authorizer now reports that the manager's authority was
        # revoked after enqueue but before handler execution.
        fresh_controller = ExecutionController(
            self.controller.root,
            agent_assignment_verifier=lambda _request: True,
        )
        fresh_controller.register_handler(
            "dispatch-test",
            "tests.test_engineering_plan_dispatch:dispatch_test_handler",
            execution_class="read_only",
            agent_action_class="edit_assigned_paths",
        )
        EngineeringPlanDispatch(
            store=self.plan_store,
            controller=fresh_controller,
            recovery_bridge=self.recovery_bridge,
        )
        fresh_approvals = DurableApprovalStore(
            fresh_controller,
            approver_authorizer=lambda _approver, _role, _scope, _roles: False,
            context_provider=lambda _request: dict(self.context),
        )
        resumed_guard = RuntimeExecutionGuard.resume(guard.snapshot())
        self._root(
            fresh_controller,
            fresh_approvals,
            request,
            record,
            resumed_guard,
            checkpoint_digest,
        )

        with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "approval is revoked"):
            fresh_controller.run_once(task.id)
        self.assertEqual("failed", fresh_controller.task(task.id).state)
        self.assertEqual([], DISPATCH_HANDLER_CALLS)


if __name__ == "__main__":
    unittest.main()
