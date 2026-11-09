from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3
from contextlib import closing
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
from portable.continuous_execution import ContinuousExecutionSupervisor
from portable.agent_org_runtime import authorize_agent_assignment
from tests.test_engineering_plan_dispatch import DISPATCH_HANDLER_CALLS, prepared


class TrustedPlanDispatchRootTests(unittest.TestCase):
    def _approval(
        self, controller, *, context, plan_task_id="implement", expiry_seconds=3600,
        clock=None, requester_id="worker-1", approver_id="manager-1",
        separation_of_duties=True, operation_suffix="",
    ):
        approval_now = clock() if clock is not None else datetime.now(timezone.utc)
        store = DurableApprovalStore(
            controller,
            approver_authorizer=lambda approver, role, scope, roles: (
                approver in {"manager-1", "worker-1"} and role in roles and scope == "repo:write"
            ),
            context_provider=lambda request: dict(context),
            clock=clock,
        )
        request = ActionRequest(
            run_id="approval-run", action_id="publish-change", actor="agent:implementer",
            capability="publish", action="publish", target="repo:main",
            risk="external_effect", idempotency_key="publish:trusted-root" + operation_suffix,
            evidence_refs=("sha256:" + "d" * 64,), task_id="approval-task",
        )
        record = store.create_request(
            request=request, operation_id="publish:trusted-root" + operation_suffix,
            artifact_digest=context["artifact_digest"],
            evidence_digest=context["evidence_digest"],
            policy_digest=context["policy_digest"],
            requester_id=requester_id, required_role="engineering_manager",
            scope="repo:write", separation_of_duties=separation_of_duties,
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
            record.approval_id, "approve", approver_id=approver_id,
            roles=("engineering_manager",), scope="repo:write",
        )
        return store, request, record

    def _assignment_resolver(self, context):
        return authorize_agent_assignment(
            role_id=getattr(self, "assignment_role_id", "BE-01"),
            manager_id=getattr(self, "assignment_manager_id", "EM-ENG-01"),
            action_class=self.assignment_action_class, scope=self.assignment_scope,
            principal_id=getattr(self, "assignment_principal_id", "worker-1"),
            authorizer=lambda request: (
                self.assignment_allowed
                and request.get("principal_id") == getattr(self, "assignment_principal_id", "worker-1")
                and (
                    request.get("scope") == self.allowed_assignment_scope
                    or self.assignment_allow_scope_widening
                )
            ),
            policy_path=self.assignment_policy_path,
            work_key=context["dispatch_key"], task_payload=context["payload"],
            deadline_seconds=context.get("assignment_deadline_seconds", context["deadline_seconds"]),
        )

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
            assignment_resolver=self._assignment_resolver,
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        plan, run, packet, _, store, controller, bridge = prepared(self.temp.name)
        self.plan = plan
        self.packet = packet
        self.plan_store = store
        self.controller = controller
        self.controller.register_handler(
            "dispatch-test", "tests.test_engineering_plan_dispatch:dispatch_test_handler",
            execution_class="read_only", agent_action_class="edit_assigned_paths",
        )
        self.recovery_bridge = bridge.recovery_bridge
        self.assignment_allowed = True
        self.assignment_role_id = "BE-01"
        self.assignment_manager_id = "EM-ENG-01"
        self.allowed_assignment_scope = ("tests",)
        self.assignment_allow_scope_widening = False
        self.assignment_action_class = "edit_assigned_paths"
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

    def test_continuous_supervisor_reconciles_parent_goal_through_trusted_dispatch_root(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(
            self.controller, approvals, request, record, guard, context=self.context,
        )
        dispatched = []
        handler_calls_before = len(DISPATCH_HANDLER_CALLS)

        def reconcile_goal():
            if not dispatched:
                task = root.dispatch(
                    run_id=self.plan.run_id,
                    task_id="implement",
                    dispatch_key="goal:dispatch-run:implement",
                    handler="dispatch-test",
                    packet=self.packet,
                    payload={"resource": "tests"},
                )
                dispatched.append(task.id)
            return {"status": "in_progress", "next_action": "verify_parent_acceptance"}

        supervisor = ContinuousExecutionSupervisor(
            self.controller,
            goal_reconcilers={"AUREN:test:trusted-parent-goal": reconcile_goal},
        )
        report = supervisor.run(poll_seconds=0.1, batch_size=10, max_cycles=2)

        self.assertEqual(1, len(dispatched))
        self.assertEqual("completed", self.controller.task(dispatched[0]).state)
        self.assertTrue(report["audit_chain_valid"])
        self.assertTrue(supervisor.verify_audit_chain())
        events = supervisor.status(event_limit=100)["recent_events"]
        self.assertTrue(any(
            event["event_type"] == "goal_reconciled"
            and event["payload"]["goal"] == "AUREN:test:trusted-parent-goal"
            for event in events
        ))
        self.assertEqual(handler_calls_before + 1, len(DISPATCH_HANDLER_CALLS))
        self.assertEqual("goal:dispatch-run:implement", DISPATCH_HANDLER_CALLS[-1]["work_key"])

    def test_supervisor_dispatches_dependency_ready_followup_through_trusted_root(self):
        import hashlib
        from portable.engineering_context_packet import build_context_packet
        from portable.engineering_plan import new_plan_run
        from portable.engineering_plan_recovery import EngineeringPlanRecoveryBridge
        from portable.engineering_plan_store import EngineeringPlanStore
        from portable.engineering_recovery import EngineeringRecoveryLedger

        handler_calls_before = len(DISPATCH_HANDLER_CALLS)
        tasks = tuple(
            replace(item, depends_on=("implement",))
            if item.task_id == "review" else item
            for item in self.plan.tasks
        )
        plan = replace(self.plan, tasks=tasks)
        run = new_plan_run(plan).transition_task(
            "implement", "ready", plan=plan, reason="first dependency-ready unit",
        )
        store = EngineeringPlanStore(Path(self.temp.name) / "sequential-plans.sqlite3")
        store.create(plan, run)
        recovery = EngineeringPlanRecoveryBridge(
            store=store,
            ledger=EngineeringRecoveryLedger(Path(self.temp.name) / "sequential-recovery.sqlite3"),
        )
        recovery.ledger.start_run(plan.run_id, recovery._intent(plan))
        recovery.ledger.checkpoint(
            plan.run_id, recovery.PHASE, status="in_progress",
            head_sha=plan.source_revision,
            payload=recovery._checkpoint_payload(
                plan, run, next_safe_action="dispatch_first_dependency_ready_unit",
            ),
        )
        self.plan = plan
        self.plan_store = store
        self.recovery_bridge = recovery
        self.packet = build_context_packet(
            plan=plan, run=run, task_id="implement",
            policy_constraints=("Do not weaken security gates",),
            budget_remaining={"attempts": 4, "dispatches": 2},
            next_safe_action="Implement the first bounded stock-research unit",
        )
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=12, max_attempts=6)
        root = TrustedPlanDispatchRoot(
            controller=self.controller,
            store=store,
            recovery_bridge=recovery,
            approval_store=approvals,
            budget_resolver=lambda _run_id, _task_id, _handler: {
                "budget_id": "crew:stock-research:run-1",
                "guard": guard,
                "checkpoint_digest": guard.snapshot()["digest"],
                "policy_digest": "a" * 64,
            },
            approval_resolver=lambda _run_id, task_id, _handler: (
                {"approval_id": record.approval_id, "request": request}
                if task_id == "implement" else None
            ),
            assignment_resolver=self._assignment_resolver,
        )
        self.controller.register_handler(
            "dispatch-review", "tests.test_engineering_plan_dispatch:dispatch_test_handler",
            execution_class="read_only", agent_action_class="run_tests",
        )
        dispatched = {}
        reconciliation_diagnostics = []

        def reconcile_goal():
            nonlocal request, record
            current_plan, current_run = recovery.load_verified(plan.run_id)
            for task_id in ("implement", "review"):
                progress = next(item for item in current_run.tasks if item.task_id == task_id)
                if task_id in dispatched:
                    durable = self.controller.task(dispatched[task_id])
                    if durable is not None and durable.state == "completed" and progress.status == "running":
                        recovery.transition_task(
                            plan.run_id, task_id, "verifying",
                            reason="canonical controller reports completed task",
                        )
                        evidence = "sha256:" + hashlib.sha256(
                            f"trusted-dispatch:{task_id}:{durable.id}:completed".encode("utf-8")
                        ).hexdigest()
                        recovery.transition_task(
                            plan.run_id, task_id, "passed",
                            reason="test handler completion receipt verified",
                            evidence_refs=(evidence,),
                        )
                        current_plan, current_run = recovery.load_verified(plan.run_id)
                    elif durable is not None and durable.state in {"failed", "cancelled"}:
                        task_events = [
                            event for event in self.controller.progress_journal._events()
                            if event.get("task_id") == durable.id
                        ]
                        with closing(sqlite3.connect(self.controller.company.db_path)) as company_db:
                            company_events = company_db.execute(
                                "SELECT kind,status,detail FROM events WHERE work_unit_id=? ORDER BY created_at",
                                (durable.work_unit_id,),
                            ).fetchall()
                        reconciliation_diagnostics.append({
                            "failed_task": task_id,
                            "durable_state": durable.state,
                            "progress_events": task_events[-12:],
                            "company_events": company_events,
                        })
                        raise RuntimeError(f"{task_id} did not complete successfully")
            if all(item.status == "passed" for item in current_run.tasks):
                return {"status": "complete", "passed_tasks": len(current_run.tasks)}

            # Promote dependency-satisfied pending units before selecting the
            # next ready unit. Otherwise a follow-up task can remain pending
            # forever even after every prerequisite has passed.
            for candidate in current_plan.tasks:
                candidate_state = next(
                    item for item in current_run.tasks if item.task_id == candidate.task_id
                )
                if (
                    candidate.task_id not in dispatched
                    and candidate_state.status == "pending"
                    and set(candidate.depends_on) <= {
                        item.task_id for item in current_run.tasks if item.status == "passed"
                    }
                ):
                    recovery.transition_task(
                        plan.run_id, candidate.task_id, "ready",
                        reason="all declared dependencies have passed",
                    )
                    current_plan, current_run = recovery.load_verified(plan.run_id)

            ready_ids = set(current_run.ready_tasks(current_plan))
            next_task = next((
                task_id for task_id in ("implement", "review")
                if task_id not in dispatched and task_id in ready_ids
            ), None)
            if next_task is None:
                reconciliation_diagnostics.append({
                    "dispatched": sorted(dispatched),
                    "progress": {item.task_id: item.status for item in current_run.tasks},
                    "ready_ids": sorted(ready_ids),
                })
                return {"status": "in_progress", "next_action": "wait_for_active_task"}

            self.assignment_manager_id = "EM-ENG-01"
            self.assignment_scope = ("tests",)
            if next_task == "implement":
                self.assignment_role_id = "BE-01"
                self.assignment_principal_id = "worker-1"
                self.assignment_action_class = "edit_assigned_paths"
                handler = "dispatch-test"
                policy_constraints = ("Do not weaken security gates",)
                next_action = "Implement the first bounded stock-research unit"
            else:
                self.assignment_role_id = "QA-01"
                self.assignment_principal_id = "qa-worker-1"
                self.assignment_action_class = "run_tests"
                handler = "dispatch-review"
                policy_constraints = ("Verify implementation evidence; do not approve own work",)
                next_action = "Run independent review tests for the stock-research unit"

            # Pin the dispatch packet to the run revision that the durable intent
            # will validate. Advancing ready -> running after queue insertion
            # invalidates that immutable binding before worker preflight.
            recovery.transition_task(
                plan.run_id, next_task, "running",
                reason="reserve the plan task revision before trusted dispatch",
            )
            current_plan, current_run = recovery.load_verified(plan.run_id)
            packet = build_context_packet(
                plan=current_plan, run=current_run, task_id=next_task,
                policy_constraints=policy_constraints,
                budget_remaining={"attempts": 4, "dispatches": 2},
                next_safe_action=next_action,
            )
            if next_task == "implement":
                # The approval receipt must bind the same post-transition
                # plan/run revision as the immutable dispatch packet.
                self.packet = packet
                _, request, record = self._approval(
                    self.controller, context=self.context,
                    operation_suffix=f":run-revision-{current_run.revision}",
                )
            task = root.dispatch(
                run_id=plan.run_id, task_id=next_task,
                dispatch_key=f"goal:dispatch-run:{next_task}",
                handler=handler, packet=packet, payload={"resource": "tests"},
            )
            dispatched[next_task] = task.id
            return {"status": "in_progress", "next_action": f"wait_for_{next_task}"}

        supervisor = ContinuousExecutionSupervisor(
            self.controller,
            goal_reconcilers={"AUREN:test:stock-research-parent-goal": reconcile_goal},
        )
        report = supervisor.run(poll_seconds=0.1, batch_size=10, max_cycles=4)

        self.assertEqual(
            {"implement", "review"}, set(dispatched),
            "reconciliation_diagnostics=" + repr(reconciliation_diagnostics)
            + "; supervisor_events=" + repr(supervisor.status(event_limit=100)["recent_events"]),
        )
        self.assertEqual(2, len(DISPATCH_HANDLER_CALLS) - handler_calls_before)
        self.assertTrue(all(self.controller.task(task_id).state == "completed" for task_id in dispatched.values()))
        _, final_run = recovery.load_verified(plan.run_id)
        self.assertTrue(all(item.status == "passed" for item in final_run.tasks))
        checkpoint = recovery.ledger.get_checkpoint(plan.run_id, recovery.PHASE)
        self.assertEqual("verified", checkpoint.status)
        self.assertTrue(report["audit_chain_valid"])
        self.assertTrue(supervisor.verify_audit_chain())


    def test_declared_plan_owner_must_match_host_authorized_role_before_queue_insert(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        from portable.engineering_plan import EngineeringPlan, PlanTask, new_plan_run
        from portable.engineering_plan_store import EngineeringPlanStore
        from portable.engineering_plan_recovery import EngineeringPlanRecoveryBridge
        from portable.engineering_recovery import EngineeringRecoveryLedger
        changed_tasks = tuple(
            PlanTask(
                item.task_id, item.title, item.acceptance_criteria, item.rollback_plan,
                depends_on=item.depends_on, risk=item.risk,
                owner_role_id="QA-01" if item.task_id == "implement" else item.owner_role_id,
                manager_role_id=item.manager_role_id,
            ) for item in self.plan.tasks
        )
        changed_plan = EngineeringPlan(
            run_id=self.plan.run_id, goal=self.plan.goal, requirements=self.plan.requirements,
            tasks=changed_tasks, source_revision=self.plan.source_revision,
            policy_digest=self.plan.policy_digest, budget=self.plan.budget,
        )
        changed_store = EngineeringPlanStore(self.temp.name + "/changed-owner-plans.sqlite3")
        changed_store.create(changed_plan, new_plan_run(changed_plan))
        changed_bridge = EngineeringPlanRecoveryBridge(
            store=changed_store,
            ledger=EngineeringRecoveryLedger(Path(self.temp.name) / "changed-owner-recovery.sqlite3"),
        )
        root = TrustedPlanDispatchRoot(
            controller=self.controller, store=changed_store, recovery_bridge=changed_bridge,
            approval_store=approvals,
            budget_resolver=lambda run_id, task_id, handler: {
                "budget_id": "crew:team-a:run-1", "guard": guard,
                "checkpoint_digest": guard.snapshot()["digest"], "policy_digest": "a" * 64,
            },
            approval_resolver=lambda run_id, task_id, handler: {
                "approval_id": record.approval_id, "request": request,
            },
            assignment_resolver=self._assignment_resolver,
        )
        with self.assertRaisesRegex(PermissionError, "does not match the declared plan task owner"):
            root.dispatch(
                run_id=changed_plan.run_id, task_id="implement", dispatch_key="owner-role-mismatch",
                handler="dispatch-test", packet=self.packet, payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_ownerless_legacy_plan_task_is_denied_before_queue_insert(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        from portable.engineering_plan import EngineeringPlan, PlanTask, new_plan_run
        from portable.engineering_plan_store import EngineeringPlanStore
        from portable.engineering_plan_recovery import EngineeringPlanRecoveryBridge
        from portable.engineering_recovery import EngineeringRecoveryLedger
        ownerless_tasks = tuple(
            PlanTask(
                item.task_id, item.title, item.acceptance_criteria, item.rollback_plan,
                depends_on=item.depends_on, risk=item.risk,
            ) for item in self.plan.tasks
        )
        ownerless_plan = EngineeringPlan(
            run_id=self.plan.run_id, goal=self.plan.goal, requirements=self.plan.requirements,
            tasks=ownerless_tasks, source_revision=self.plan.source_revision,
            policy_digest=self.plan.policy_digest, budget=self.plan.budget,
        )
        ownerless_store = EngineeringPlanStore(self.temp.name + "/ownerless-plans.sqlite3")
        ownerless_store.create(ownerless_plan, new_plan_run(ownerless_plan))
        ownerless_bridge = EngineeringPlanRecoveryBridge(
            store=ownerless_store,
            ledger=EngineeringRecoveryLedger(Path(self.temp.name) / "ownerless-recovery.sqlite3"),
        )
        root = TrustedPlanDispatchRoot(
            controller=self.controller, store=ownerless_store, recovery_bridge=ownerless_bridge,
            approval_store=approvals,
            budget_resolver=lambda run_id, task_id, handler: {
                "budget_id": "crew:team-a:run-1", "guard": guard,
                "checkpoint_digest": guard.snapshot()["digest"], "policy_digest": "a" * 64,
            },
            approval_resolver=lambda run_id, task_id, handler: {
                "approval_id": record.approval_id, "request": request,
            },
            assignment_resolver=self._assignment_resolver,
        )
        with self.assertRaisesRegex(PermissionError, "must declare an immutable owner_role_id"):
            root.dispatch(
                run_id=ownerless_plan.run_id, task_id="implement", dispatch_key="ownerless-plan-task",
                handler="dispatch-test", packet=self.packet, payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_declared_plan_manager_must_match_host_authorized_manager_before_queue_insert(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        # The task declares CTO ownership, while the host authorizer correctly
        # identifies EM-ENG-01 as the reporting manager for BE-01.
        task = next(item for item in self.plan.tasks if item.task_id == "implement")
        from portable.engineering_plan import EngineeringPlan, PlanTask
        changed_tasks = tuple(
            PlanTask(
                item.task_id, item.title, item.acceptance_criteria, item.rollback_plan,
                depends_on=item.depends_on, risk=item.risk,
                owner_role_id=item.owner_role_id,
                manager_role_id="CTO-01" if item.task_id == "implement" else item.manager_role_id,
            ) for item in self.plan.tasks
        )
        changed_plan = EngineeringPlan(
            run_id=self.plan.run_id, goal=self.plan.goal, requirements=self.plan.requirements,
            tasks=changed_tasks, source_revision=self.plan.source_revision,
            policy_digest=self.plan.policy_digest, budget=self.plan.budget,
        )
        # A fresh store avoids mutating an existing plan revision in place.
        from portable.engineering_plan_store import EngineeringPlanStore
        changed_store = EngineeringPlanStore(self.temp.name + "/changed-plans.sqlite3")
        from portable.engineering_plan import new_plan_run
        changed_store.create(changed_plan, new_plan_run(changed_plan))
        from portable.engineering_plan_recovery import EngineeringPlanRecoveryBridge
        from portable.engineering_recovery import EngineeringRecoveryLedger
        changed_bridge = EngineeringPlanRecoveryBridge(
            store=changed_store,
            ledger=EngineeringRecoveryLedger(Path(self.temp.name) / "changed-recovery.sqlite3"),
        )
        root = TrustedPlanDispatchRoot(
            controller=self.controller, store=changed_store, recovery_bridge=changed_bridge,
            approval_store=approvals,
            budget_resolver=lambda run_id, task_id, handler: {
                "budget_id": "crew:team-a:run-1", "guard": guard,
                "checkpoint_digest": guard.snapshot()["digest"], "policy_digest": "a" * 64,
            },
            approval_resolver=lambda run_id, task_id, handler: {
                "approval_id": record.approval_id, "request": request,
            },
            assignment_resolver=self._assignment_resolver,
        )
        with self.assertRaisesRegex(PermissionError, "does not match the declared plan task manager"):
            root.dispatch(
                run_id=changed_plan.run_id, task_id="implement", dispatch_key="owner-manager-mismatch",
                handler="dispatch-test", packet=self.packet, payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_revoked_approver_authority_blocks_dispatch_before_queue_insert(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        approvals.approver_authorizer = lambda approver, role, scope, roles: False
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "authority has been revoked or changed"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="revoked-approver",
                handler="dispatch-test", packet=self.packet, payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_ceo_cannot_be_substituted_as_worker_manager_before_dispatch(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        self.assignment_manager_id = "CEO-01"
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "assignment authorization failed closed"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="ceo-manager-bypass",
                handler="dispatch-test", packet=self.packet, payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_missing_host_assignment_is_denied_before_queue_insertion(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        self.assignment_allowed = False
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "assignment authorization failed"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="assignment-denied",
                handler="dispatch-test", packet=self.packet,
                payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_handler_action_class_mismatch_is_denied_before_queue_insertion(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        self.assignment_action_class = "run_scoped_tests"
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "action class does not match"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="assignment-action-mismatch",
                handler="dispatch-test", packet=self.packet,
                payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_missing_or_conflicting_resource_selectors_are_denied_before_queue_insertion(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "explicit resource"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="missing-resource",
                handler="dispatch-test", packet=self.packet, payload={"change": "small"},
            )
        with self.assertRaisesRegex(PermissionError, "outside the host-authorized assignment scope"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="conflicting-resource-alias",
                handler="dispatch-test", packet=self.packet,
                payload={"resource": "tests", "path": "secrets"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_assignment_policy_drift_cannot_replay_an_existing_dispatch(self):
        from portable.engineering_plan_dispatch import DispatchConflict
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        first = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="assignment-policy-replay",
            handler="dispatch-test", packet=self.packet,
                payload={"resource": "tests"},
        )
        self.assignment_policy_path.write_text(
            '{"schema_version":"auren-guard-v1","default_effect":"deny","rules":[]}',
            encoding="utf-8",
        )
        with self.assertRaises(DispatchConflict):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="assignment-policy-replay",
                handler="dispatch-test", packet=self.packet,
                payload={"resource": "tests"},
            )
        self.assertEqual("tests", first.payload["_auren_agent_assignment"]["scope"][0])

    def test_host_scope_policy_denies_widened_assignment_before_queue_insert(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        self.assignment_scope = ("tests", "secrets")
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "assignment authorization failed"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="assignment-scope-denied",
                handler="dispatch-test", packet=self.packet,
                payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_scope_widening_cannot_replay_an_existing_assignment(self):
        from portable.engineering_plan_dispatch import DispatchConflict
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        first = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="assignment-scope-widening",
            handler="dispatch-test", packet=self.packet,
                payload={"resource": "tests"},
        )
        self.assignment_scope = ("tests", "secrets")
        self.assignment_allow_scope_widening = True
        with self.assertRaises(DispatchConflict):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="assignment-scope-widening",
                handler="dispatch-test", packet=self.packet,
                payload={"resource": "tests"},
            )
        self.assertEqual(["tests"], first.payload["_auren_agent_assignment"]["scope"])

    def test_revoked_assignment_is_rejected_at_worker_preflight(self):
        from portable.engineering_plan_recovery import EngineeringPlanCheckpointMismatch
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        task = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="assignment-revocation",
            handler="dispatch-test", packet=self.packet,
                payload={"resource": "tests"},
        )
        # Reconstruct the controller from the same durable root to model a fresh process.
        self.assignment_allowed = False
        fresh_controller = ExecutionController(
            self.controller.root,
            agent_assignment_verifier=lambda _request: self.assignment_allowed,
        )
        fresh_controller.register_handler(
            "dispatch-test", "tests.test_engineering_plan_dispatch:dispatch_test_handler",
            execution_class="read_only", agent_action_class="edit_assigned_paths",
        )
        EngineeringPlanDispatch(
            store=self.plan_store,
            controller=fresh_controller,
            recovery_bridge=self.recovery_bridge,
        )
        TrustedPlanDispatchRoot(
            controller=fresh_controller,
            store=self.plan_store,
            recovery_bridge=self.recovery_bridge,
            approval_store=DurableApprovalStore(fresh_controller),
            budget_resolver=lambda run_id, task_id, handler: {
                "budget_id": "crew:team-a:run-1", "guard": guard,
                "checkpoint_digest": guard.snapshot()["digest"], "policy_digest": "a" * 64,
            },
            approval_resolver=lambda run_id, task_id, handler: {
                "approval_id": record.approval_id, "request": request,
            },
            assignment_resolver=self._assignment_resolver,
        )
        DISPATCH_HANDLER_CALLS.clear()
        with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "revoked"):
            fresh_controller._verify_engineering_plan_dispatch_checkpoint(
                fresh_controller.task(task.id)
            )
        with self.assertRaisesRegex(EngineeringPlanCheckpointMismatch, "revoked"):
            fresh_controller.run_once(task.id)
        self.assertEqual("failed", fresh_controller.task(task.id).state)
        self.assertEqual([], DISPATCH_HANDLER_CALLS)

    def test_assigned_plan_task_runs_through_full_controller_preflight(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        DISPATCH_HANDLER_CALLS.clear()
        task = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="full-worker-assignment",
            handler="dispatch-test", packet=self.packet, payload={"resource": "tests"},
        )
        self.assertEqual("full-worker-assignment", task.payload["work_key"])
        self.assertEqual("full-worker-assignment", task.payload["_auren_agent_assignment"]["work_key"])
        self.assertEqual("full-worker-assignment", self.controller.task(task.id).payload["work_key"])
        self.controller.run_once(task.id)
        self.assertEqual("completed", self.controller.task(task.id).state)
        self.assertEqual(1, len(DISPATCH_HANDLER_CALLS))

    def test_root_resolves_live_budget_and_approved_record_and_binds_them(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        task = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="trusted-dispatch-1",
            handler="dispatch-test", packet=self.packet, payload={"resource": "tests", "change": "small"},
        )
        binding = task.payload["auren_dispatch"]
        self.assertEqual("crew:team-a:run-1", binding["shared_budget_binding"]["budget_id"])
        self.assertEqual(guard.snapshot()["digest"], binding["shared_budget_binding"]["checkpoint_digest"])
        self.assertEqual("a" * 64, binding["shared_budget_binding"]["policy_digest"])
        self.assertEqual(record.approval_id, binding["approval_evidence"]["approval_id"])
        self.assertEqual(record.evidence_digest, binding["approval_evidence"]["evidence_digest"])
        self.assertEqual("approved", binding["approval_evidence"]["decision"])
        self.assertEqual("manager-1", binding["approval_evidence"]["approver_id"])
        self.assertEqual("worker-1", binding["approval_evidence"]["implementer_principal_id"])
        self.assertEqual("BE-01", binding["approval_evidence"]["implementer_role_id"])
        self.assertEqual("worker-1", binding["assignment_evidence"]["principal_id"])
        self.assertEqual("auren-agent-assignment-v4", binding["assignment_evidence"]["schema_version"])
        self.assertEqual("BE-01", binding["assignment_evidence"]["role_id"])
        self.assertEqual(binding["assignment_evidence"], task.payload["_auren_agent_assignment"])
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
            assignment_resolver=self._assignment_resolver,
        )
        with self.assertRaisesRegex(PermissionError, "not approved"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="pending-approval",
                handler="dispatch-test", packet=self.packet,
                payload={"resource": "tests"},
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
                assignment_resolver=self._assignment_resolver,
            ).dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="cancelled-budget",
                handler="dispatch-test", packet=self.packet,
                payload={"resource": "tests"},
            )

    def test_approval_requester_must_match_authenticated_implementer_before_queue(self):
        store, request, record = self._approval(
            self.controller, context=self.context, requester_id="different-worker",
        )
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, store, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "requester does not match the authenticated implementer"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement",
                dispatch_key="reviewer-requester-mismatch",
                handler="dispatch-test", packet=self.packet, payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        with sqlite3.connect(self.controller.company.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM work_units").fetchone()[0], 0)

    def test_tampered_approval_requester_row_cannot_override_audit_actor(self):
        store, request, record = self._approval(
            self.controller, context=self.context, requester_id="different-worker",
        )
        with sqlite3.connect(self.controller.db_path) as db:
            db.execute(
                "UPDATE durable_approvals SET requester_id=? WHERE approval_id=?",
                ("worker-1", record.approval_id),
            )
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, store, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "requester differs from its immutable audit actor"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement",
                dispatch_key="tampered-requester-audit-actor",
                handler="dispatch-test", packet=self.packet, payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_implementer_cannot_self_approve_even_when_legacy_separation_flag_is_false(self):
        store, request, record = self._approval(
            self.controller, context=self.context,
            requester_id="worker-1", approver_id="worker-1",
            separation_of_duties=False,
        )
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, store, request, record, guard, context=self.context)
        with self.assertRaisesRegex(PermissionError, "implementer self-approval is not permitted"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement",
                dispatch_key="implementer-self-approval",
                handler="dispatch-test", packet=self.packet, payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

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
                payload={"resource": "tests"},
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
                payload={"resource": "tests"},
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
                assignment_resolver=self._assignment_resolver,
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
                payload={"resource": "tests"},
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
                payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])


    def test_caller_deadline_cannot_extend_supervisor_budget(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4, deadline_seconds=60)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        task = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="bounded-supervisor-deadline",
            handler="dispatch-test", packet=self.packet, payload={"resource": "tests"}, deadline_seconds=300,
        )
        with sqlite3.connect(self.controller.db_path) as db:
            deadline_seconds = db.execute(
                "SELECT deadline_seconds FROM tasks WHERE id=?", (task.id,),
            ).fetchone()[0]
        self.assertGreater(deadline_seconds, 0)
        # Monotonic-clock subtraction can produce a few ULPs above the
        # configured duration; allow only floating-point representation noise.
        self.assertLessEqual(deadline_seconds, 60.0 + 1e-6)


    def test_worker_resume_requires_trusted_root_bootstrap(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        root = self._root(self.controller, approvals, request, record, guard, context=self.context)
        task = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="resume-root-required",
            handler="dispatch-test", packet=self.packet, payload={"resource": "tests", "change": "small"},
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
            handler="dispatch-test", packet=self.packet, payload={"resource": "tests", "change": "small"},
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
                payload={"resource": "tests"},
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
            assignment_resolver=self._assignment_resolver,
        )
        with self.assertRaisesRegex(PermissionError, "requires a canonical durable approval"):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="missing-approval",
                handler="dispatch-test", packet=self.packet,
                payload={"resource": "tests"},
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
                payload={"resource": "tests"},
        )
        admission_digest = first.payload["auren_dispatch"]["shared_budget_binding"]["checkpoint_digest"]
        self.assertTrue(guard.charge_attempt("specialist:implementation"))
        replay = root.dispatch(
            run_id=self.plan.run_id, task_id="implement", dispatch_key="stable-budget-replay",
            handler="dispatch-test", packet=self.packet,
                payload={"resource": "tests"},
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
                payload={"resource": "tests"},
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
                payload={"resource": "tests"},
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
            assignment_resolver=self._assignment_resolver,
        )
        with self.assertRaises(ExecutionBudgetExceeded):
            root.dispatch(
                run_id=self.plan.run_id, task_id="implement", dispatch_key="cancel-during-approval",
                handler="dispatch-test", packet=self.packet,
                payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])



    def _assert_assignment_denied_before_queue_insert(
        self, *, role_id: str, manager_id: str, action_class: str,
    ):
        handler = "executive-scope-boundary"
        self.controller.register_handler(
            handler,
            "tests.test_engineering_plan_dispatch:dispatch_test_handler",
            execution_class="read_only",
            agent_action_class=action_class,
        )
        self.assignment_role_id = role_id
        self.assignment_manager_id = manager_id
        self.assignment_action_class = action_class
        self.assignment_scope = ("tests",)
        self.allowed_assignment_scope = ("tests",)
        self.assignment_allowed = True

        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        admission_digest = guard.snapshot()["digest"]
        approvals = DurableApprovalStore(
            self.controller,
            approver_authorizer=lambda _approver, _role, _scope, _roles: True,
            context_provider=lambda _request: dict(self.context),
        )
        root = TrustedPlanDispatchRoot(
            controller=self.controller,
            store=self.plan_store,
            recovery_bridge=self.recovery_bridge,
            approval_store=approvals,
            budget_resolver=lambda _run, _task, _handler: {
                "budget_id": "crew:team-a:run-1",
                "guard": guard,
                "checkpoint_digest": admission_digest,
                "policy_digest": "a" * 64,
            },
            approval_resolver=lambda _run, _task, _handler: None,
            assignment_resolver=self._assignment_resolver,
        )
        dispatch_key = "executive-scope-denial:" + role_id + ":" + action_class
        with self.assertRaisesRegex(PermissionError, "host agent assignment authorization failed closed"):
            root.dispatch(
                run_id=self.plan.run_id,
                task_id="implement",
                dispatch_key=dispatch_key,
                handler=handler,
                packet=self.packet,
                payload={"resource": "tests", "work_key": dispatch_key},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(
                0,
                db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                "a rejected executive assignment must not create a durable task",
            )

    def test_ceo_cannot_assign_cto_through_the_canonical_dispatch_boundary(self):
        self._assert_assignment_denied_before_queue_insert(
            role_id="CTO-01",
            manager_id="CEO-01",
            action_class="prioritize_technical_work",
        )

    def test_cto_cannot_delegate_architecture_approval_outside_manager_scope(self):
        self._assert_assignment_denied_before_queue_insert(
            role_id="EM-ENG-01",
            manager_id="CTO-01",
            action_class="approve_architecture_within_policy",
        )

    def test_forbidden_executive_gate_actions_cannot_be_registered_as_handlers(self):
        for action_class in (
            "bypass_security_or_release_gates",
            "waive_required_checks",
        ):
            handler = "forbidden-gate-action-" + action_class
            with self.subTest(action_class=action_class):
                with self.assertRaisesRegex(ValueError, "not declared by any organization role"):
                    self.controller.register_handler(
                        handler,
                        "tests.test_engineering_plan_dispatch:dispatch_test_handler",
                        execution_class="read_only",
                        agent_action_class=action_class,
                    )
                with sqlite3.connect(self.controller.db_path) as db:
                    self.assertIsNone(
                        db.execute("SELECT 1 FROM handlers WHERE name=?", (handler,)).fetchone()
                    )

    def test_assignment_role_and_scope_survive_fresh_process_resume(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        admission_digest = guard.snapshot()["digest"]
        root = self._root(
            self.controller, approvals, request, record, guard, context=self.context,
        )
        DISPATCH_HANDLER_CALLS.clear()
        task = root.dispatch(
            run_id=self.plan.run_id,
            task_id="implement",
            dispatch_key="assignment-fresh-process-resume",
            handler="dispatch-test",
            packet=self.packet,
            payload={"resource": "tests"},
        )
        recorded_assignment = dict(task.payload["_auren_agent_assignment"])
        self.assertEqual("BE-01", recorded_assignment["role_id"])
        self.assertEqual("EM-ENG-01", recorded_assignment["manager_id"])
        self.assertEqual(["tests"], recorded_assignment["scope"])

        # Rebuild every in-memory runtime owner from the same durable stores.
        # The new root must reauthorize the persisted role and exact scope before
        # the worker can resume; the old process's assignment cache is unavailable.
        fresh_controller = ExecutionController(
            self.controller.root,
            agent_assignment_verifier=lambda _request: self.assignment_allowed,
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
            approver_authorizer=lambda approver, role, scope, roles: (
                approver == "manager-1" and role in roles and scope == "repo:write"
            ),
            context_provider=lambda _request: dict(self.context),
        )
        resumed_guard = RuntimeExecutionGuard.resume(guard.snapshot())
        TrustedPlanDispatchRoot(
            controller=fresh_controller,
            store=self.plan_store,
            recovery_bridge=self.recovery_bridge,
            approval_store=fresh_approvals,
            budget_resolver=lambda _run, _task, _handler: {
                "budget_id": "crew:team-a:run-1",
                "guard": resumed_guard,
                "checkpoint_digest": admission_digest,
                "policy_digest": "a" * 64,
            },
            approval_resolver=lambda _run, _task, _handler: {
                "approval_id": record.approval_id,
                "request": request,
            },
            assignment_resolver=self._assignment_resolver,
        )

        fresh_task = fresh_controller.task(task.id)
        self.assertEqual(recorded_assignment, fresh_task.payload["_auren_agent_assignment"])
        fresh_controller._verify_engineering_plan_dispatch_checkpoint(fresh_task)
        fresh_controller.run_once(task.id)
        self.assertEqual("completed", fresh_controller.task(task.id).state)
        self.assertEqual(1, len(DISPATCH_HANDLER_CALLS))
        self.assertEqual(
            recorded_assignment,
            fresh_controller.task(task.id).payload["_auren_agent_assignment"],
        )


    def test_supervisor_cancellation_during_assignment_authorization_blocks_queue_insert(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4)
        admission_digest = guard.snapshot()["digest"]
        original_resolver = self._assignment_resolver

        def resolve_assignment(context):
            assignment = original_resolver(context)
            guard.cancel("cancelled while host assignment authorization was resolving")
            return assignment

        root = TrustedPlanDispatchRoot(
            controller=self.controller,
            store=self.plan_store,
            recovery_bridge=self.recovery_bridge,
            approval_store=approvals,
            budget_resolver=lambda _run, _task, _handler: {
                "budget_id": "crew:team-a:run-1",
                "guard": guard,
                "checkpoint_digest": admission_digest,
                "policy_digest": "a" * 64,
            },
            approval_resolver=lambda _run, _task, _handler: {
                "approval_id": record.approval_id,
                "request": request,
            },
            assignment_resolver=resolve_assignment,
        )
        with self.assertRaises(ExecutionBudgetExceeded):
            root.dispatch(
                run_id=self.plan.run_id,
                task_id="implement",
                dispatch_key="cancel-during-assignment",
                handler="dispatch-test",
                packet=self.packet,
                payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_expired_supervisor_deadline_during_assignment_blocks_queue_insert(self):
        approvals, request, record = self._approval(self.controller, context=self.context)
        guard = RuntimeExecutionGuard(
            max_steps=8, max_attempts=4, deadline_seconds=30,
        )
        admission_digest = guard.snapshot()["digest"]
        original_resolver = self._assignment_resolver

        def resolve_assignment(context):
            assignment = original_resolver(context)
            guard.tighten_deadline(0.001)
            # Let the monotonic deadline elapse on Windows and Unix alike;
            # a sub-microsecond sleep is not a portable scheduling guarantee.
            import time
            time.sleep(0.02)
            return assignment

        root = TrustedPlanDispatchRoot(
            controller=self.controller,
            store=self.plan_store,
            recovery_bridge=self.recovery_bridge,
            approval_store=approvals,
            budget_resolver=lambda _run, _task, _handler: {
                "budget_id": "crew:team-a:run-1",
                "guard": guard,
                "checkpoint_digest": admission_digest,
                "policy_digest": "a" * 64,
            },
            approval_resolver=lambda _run, _task, _handler: {
                "approval_id": record.approval_id,
                "request": request,
            },
            assignment_resolver=resolve_assignment,
        )
        with self.assertRaises(ExecutionBudgetExceeded):
            root.dispatch(
                run_id=self.plan.run_id,
                task_id="implement",
                dispatch_key="deadline-during-assignment",
                handler="dispatch-test",
                packet=self.packet,
                payload={"resource": "tests"},
            )
        with sqlite3.connect(self.controller.db_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])



if __name__ == "__main__":
    unittest.main()
