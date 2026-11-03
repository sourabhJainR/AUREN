from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest

from portable.agent_execution_guard import AgentAssignmentScopeDenied
from portable.agent_org_runtime import authorize_agent_assignment
from portable.durable_approval import DurableApprovalStore
from portable.engineering_plan_dispatch import EngineeringPlanDispatch
from portable.execution_controller import ExecutionController
from portable.trusted_plan_dispatch import TrustedPlanDispatchRoot
from portable.runtime_recovery import RuntimeExecutionGuard
from tests.test_engineering_plan_dispatch import DISPATCH_HANDLER_CALLS, prepared


class TrustedPlanProfileBindingTests(unittest.TestCase):
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
            "profile-dispatch",
            "tests.test_engineering_plan_dispatch:dispatch_test_handler",
            execution_class="read_only",
            agent_action_class="edit_assigned_paths",
        )

    def _assignment_resolver(self, context):
        return authorize_agent_assignment(
            role_id="BE-01",
            manager_id="EM-ENG-01",
            action_class="edit_assigned_paths",
            scope=["tests"],
            authorizer=lambda _request: True,
            work_key=context["dispatch_key"],
            task_payload=context["payload"],
            deadline_seconds=context.get("assignment_deadline_seconds", context.get("deadline_seconds")),
        )

    def _root(self, controller):
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        approvals = DurableApprovalStore(controller)
        return TrustedPlanDispatchRoot(
            controller=controller,
            store=self.plan_store,
            recovery_bridge=self.recovery_bridge,
            approval_store=approvals,
            budget_resolver=lambda _run, _task, _handler: {
                "budget_id": "crew:profile-test",
                "guard": guard,
                "checkpoint_digest": guard.snapshot()["digest"],
                "policy_digest": "a" * 64,
            },
            approval_resolver=lambda _run, _task, _handler: None,
            assignment_resolver=self._assignment_resolver,
        )

    def test_profile_revision_and_digest_are_pinned_into_durable_task(self):
        root = self._root(self.controller)
        task = root.dispatch(
            run_id=self.plan.run_id,
            task_id="implement",
            dispatch_key="profile-bound-task",
            handler="profile-dispatch",
            packet=self.packet,
            payload={"resource": "tests"},
            profile_id="backend_implementer",
            profile_revision=1,
        )
        binding = task.payload["_auren_agent_profile"]
        self.assertEqual({"profile_id", "revision", "digest"}, set(binding))
        self.assertEqual("backend_implementer", binding["profile_id"])
        self.assertEqual(1, binding["revision"])
        self.assertEqual(64, len(binding["digest"]))
        self.assertEqual("BE-01", task.payload["_auren_agent_assignment"]["role_id"])

    def test_profile_role_mismatch_and_unknown_revision_are_denied_before_queue_insert(self):
        root = self._root(self.controller)
        with self.assertRaises(PermissionError):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="wrong-profile-role",
                handler="profile-dispatch", packet=self.packet, payload={"resource": "tests"},
                profile_id="principal_architect", profile_revision=1,
            )
        with self.assertRaises(ValueError):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="stale-profile-revision",
                handler="profile-dispatch", packet=self.packet, payload={"resource": "tests"},
                profile_id="backend_implementer", profile_revision=999,
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_profile_digest_tampering_blocks_fresh_process_before_handler(self):
        root = self._root(self.controller)
        task = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="profile-tamper",
            handler="profile-dispatch", packet=self.packet, payload={"resource": "tests"},
            profile_id="backend_implementer", profile_revision=1,
        )
        with sqlite3.connect(self.controller.db_path) as db:
            row = db.execute("SELECT payload FROM tasks WHERE id=?", (task.id,)).fetchone()
            payload = json.loads(row[0])
            payload["_auren_agent_profile"]["digest"] = "0" * 64
            db.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(payload), task.id))
            db.commit()

        fresh = ExecutionController(
            self.controller.root,
            agent_assignment_verifier=lambda _request: True,
        )
        fresh.register_handler(
            "profile-dispatch",
            "tests.test_engineering_plan_dispatch:dispatch_test_handler",
            execution_class="read_only",
            agent_action_class="edit_assigned_paths",
        )
        EngineeringPlanDispatch(
            store=self.plan_store, controller=fresh, recovery_bridge=self.recovery_bridge,
        )
        self._root(fresh)
        DISPATCH_HANDLER_CALLS.clear()
        with self.assertRaises(AgentAssignmentScopeDenied):
            fresh.run_once(task.id)
        self.assertEqual([], DISPATCH_HANDLER_CALLS)


if __name__ == "__main__":
    unittest.main()
