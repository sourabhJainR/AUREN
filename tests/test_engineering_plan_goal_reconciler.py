from __future__ import annotations

from contextlib import closing
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable.agent_org_runtime import authorize_agent_assignment
from portable.durable_approval import DurableApprovalStore
from portable.engineering_plan import EngineeringPlan, PlanTask, new_plan_run
from portable.engineering_plan_goal_reconciler import EngineeringPlanGoalReconciler
from portable.continuous_execution import ContinuousExecutionSupervisor
from portable.engineering_plan_recovery import EngineeringPlanRecoveryBridge
from portable.engineering_plan_store import EngineeringPlanStore
from portable.engineering_recovery import EngineeringRecoveryLedger
from portable.execution_controller import ExecutionController
from portable.runtime_recovery import RuntimeExecutionGuard
from portable.trusted_plan_dispatch import TrustedPlanDispatchRoot


def goal_implement_handler(payload):
    return {"accepted": True, "task": "implement", "resource": payload["resource"]}


def goal_review_handler(payload):
    return {"accepted": True, "task": "review", "resource": payload["resource"]}


class EngineeringPlanGoalReconcilerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.plan = EngineeringPlan(
            run_id="goal-reconcile-run",
            goal="Complete a bounded engineering goal without another prompt",
            requirements=("Implementation and independent review must pass",),
            source_revision="goal-source-revision",
            tasks=(
                PlanTask(
                    "implement", "Implement the bounded change",
                    ("implementation handler completes",), "revert implementation",
                    owner_role_id="BE-01", manager_role_id="EM-ENG-01",
                ),
                PlanTask(
                    "review", "Independently review the change",
                    ("review handler completes",), "revert review",
                    depends_on=("implement",), owner_role_id="QA-01",
                    manager_role_id="EM-ENG-01",
                ),
            ),
        )
        self.store = EngineeringPlanStore(root / "plans.sqlite3")
        self.store.create(self.plan, new_plan_run(self.plan))
        self.controller = ExecutionController(root / "runtime")
        self.controller.register_handler(
            "goal-implement", "tests.test_engineering_plan_goal_reconciler:goal_implement_handler",
            execution_class="read_only", agent_action_class="edit_assigned_paths",
        )
        self.controller.register_handler(
            "goal-review", "tests.test_engineering_plan_goal_reconciler:goal_review_handler",
            execution_class="read_only", agent_action_class="run_tests",
        )
        self.bridge = EngineeringPlanRecoveryBridge(
            store=self.store,
            ledger=EngineeringRecoveryLedger(root / "runtime" / ".auren" / "recovery.sqlite3"),
        )
        self.bridge.start(self.plan)
        self.guard = RuntimeExecutionGuard(max_steps=32, max_attempts=16)
        self.admission_digest = self.guard.snapshot()["digest"]
        self.policy_path = root / "runtime" / ".auren" / "guard_policy.json"
        self.policy_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy_path.write_text(
            '{"schema_version":"auren-guard-v1","default_effect":"allow","rules":[]}',
            encoding="utf-8",
        )
        self.approvals = DurableApprovalStore(
            self.controller,
            approver_authorizer=lambda *_args: False,
            context_provider=lambda _request: {},
        )
        self.root = self._trusted_root()

    def _trusted_root(self):
        def assignment_resolver(context):
            task = context["packet"].task
            role_id = task["owner_role_id"]
            manager_id = task["manager_role_id"]
            if role_id == "BE-01":
                principal_id, action_class = "worker-1", "edit_assigned_paths"
            else:
                principal_id, action_class = "qa-worker-1", "run_tests"
            scope = ("tests",)
            return authorize_agent_assignment(
                role_id=role_id,
                manager_id=manager_id,
                action_class=action_class,
                scope=scope,
                principal_id=principal_id,
                authorizer=lambda request: (
                    request.get("principal_id") == principal_id
                    and request.get("scope") == scope
                ),
                policy_path=self.policy_path,
                work_key=context["dispatch_key"],
                task_payload=context["payload"],
                deadline_seconds=context["deadline_seconds"],
            )

        return TrustedPlanDispatchRoot(
            controller=self.controller,
            store=self.store,
            recovery_bridge=self.bridge,
            approval_store=self.approvals,
            budget_resolver=lambda _run_id, _task_id, _handler: {
                "budget_id": "crew:goal-reconcile-run",
                "guard": self.guard,
                "checkpoint_digest": self.admission_digest,
                "policy_digest": "a" * 64,
            },
            approval_resolver=lambda _run_id, _task_id, _handler: None,
            assignment_resolver=assignment_resolver,
        )

    def _reconciler(self, *, verifier=None):
        def handler(plan, task):
            return "goal-implement" if task.task_id == "implement" else "goal-review"

        def payload(plan, task):
            return {"resource": "tests", "plan_digest": plan.digest, "task_id": task.task_id}

        def budget(_plan, _run, _task):
            return {"attempts": 4, "steps": 16, "tool_calls": 8, "tokens": 2000}

        def completion(plan, task, durable):
            if verifier is not None:
                return verifier(plan, task, durable)
            if durable.state != "completed":
                return {"verified": False, "reason": "durable task is not complete"}
            digest = hashlib.sha256(
                f"{plan.digest}:{task.task_id}:{durable.id}".encode("utf-8")
            ).hexdigest()
            return {"verified": True, "evidence_refs": (f"sha256:{digest}",)}

        return EngineeringPlanGoalReconciler(
            run_id=self.plan.run_id,
            controller=self.controller,
            recovery_bridge=self.bridge,
            trusted_dispatch_root=self.root,
            handler_resolver=handler,
            payload_resolver=payload,
            budget_resolver=budget,
            completion_verifier=completion,
            policy_constraints=("Do not weaken mandatory security gates",),
        )

    def test_reconciler_resumes_by_work_key_and_completes_dependency_chain(self):
        reconciler = self._reconciler()
        original_dispatch = self.root.dispatch
        dispatch_state = {"lose_ack": True}

        def lose_first_acknowledgement(**kwargs):
            task = original_dispatch(**kwargs)
            if dispatch_state["lose_ack"]:
                dispatch_state["lose_ack"] = False
                raise RuntimeError("simulated lost dispatch acknowledgement")
            return task

        self.root.dispatch = lose_first_acknowledgement
        first_key = reconciler.work_key(self.plan.tasks[0], 0)
        with self.assertRaisesRegex(RuntimeError, "simulated lost dispatch acknowledgement"):
            reconciler()
        first_task = self.controller.task_by_work_key(first_key)
        self.assertIsNotNone(first_task)

        # A fresh reconciler instance recovers the same pending task after the
        # submit acknowledgement was lost; it must not create a second row.
        resumed = self._reconciler()()
        self.assertEqual(resumed["active_task_id"], "implement")
        self.assertEqual(resumed["durable_task_id"], first_task.id)
        with closing(sqlite3.connect(self.controller.db_path)) as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

        self.controller.resume_pending(limit=10, continue_after_durable_failure=True)
        second = reconciler()
        self.assertEqual(second["status"], "in_progress")
        self.assertEqual(second["task_id"], "review")
        self.assertNotEqual(second["dispatch_key"], first_key)
        self.controller.resume_pending(limit=10, continue_after_durable_failure=True)

        final = reconciler()
        self.assertEqual(final["status"], "complete")
        plan, run = self.bridge.load_verified(self.plan.run_id)
        self.assertEqual({item.task_id for item in run.tasks}, {"implement", "review"})
        self.assertTrue(all(item.status == "passed" and item.evidence_refs for item in run.tasks))
        checkpoint = self.bridge.ledger.get_checkpoint(self.plan.run_id, self.bridge.PHASE)
        self.assertEqual(checkpoint.status, "verified")

    def test_unverified_completion_never_marks_task_passed(self):
        state = {"verified": False}

        def verifier(_plan, _task, durable):
            if not state["verified"]:
                return {"verified": False, "reason": "independent acceptance receipt is missing"}
            digest = hashlib.sha256(durable.id.encode("utf-8")).hexdigest()
            return {"verified": True, "evidence_refs": (f"sha256:{digest}",)}

        reconciler = self._reconciler(verifier=verifier)
        first = reconciler()
        self.assertEqual(first["task_id"], "implement")
        self.controller.resume_pending(limit=10, continue_after_durable_failure=True)

        waiting = reconciler()
        self.assertEqual(waiting["status"], "waiting_external_dependency")
        _, run = self.bridge.load_verified(self.plan.run_id)
        self.assertEqual(next(item for item in run.tasks if item.task_id == "implement").status, "running")

        state["verified"] = True
        resumed = reconciler()
        self.assertEqual(resumed["status"], "in_progress")
        self.assertEqual(resumed["task_id"], "review")


    def test_continuous_supervisor_completes_parent_goal_without_another_prompt(self):
        reconciler = self._reconciler()
        supervisor = ContinuousExecutionSupervisor(
            self.controller,
            goal_reconcilers={reconciler.goal_key: reconciler},
        )
        report = supervisor.run(poll_seconds=0.1, batch_size=10, max_cycles=3)

        self.assertTrue(report["audit_chain_valid"])
        _, run = self.bridge.load_verified(self.plan.run_id)
        self.assertTrue(all(item.status == "passed" and item.evidence_refs for item in run.tasks))
        status = supervisor.status(event_limit=100)
        self.assertEqual(2, status["task_counts"]["completed"])
        self.assertTrue(any(
            event["event_type"] == "goal_reconciled"
            and event["payload"]["goal"] == reconciler.goal_key
            and event["payload"]["outcome"]["status"] == "complete"
            for event in status["recent_events"]
        ))
