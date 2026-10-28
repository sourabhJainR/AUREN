"""Integration tests for trusted role-scoped dispatch through AUREN's canonical controller."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from portable.agent_role_scope import (
    ROLE_SCOPE_KEY,
    ROLE_SCOPE_SCHEMA_VERSION,
    RoleScopeError,
    role_policy_digest,
    validate_role_scope,
)
from portable.execution_controller import ExecutionController
from portable.engineering_context_packet import build_context_packet
from portable.engineering_plan import EngineeringPlan, PlanTask, new_plan_run
from portable.engineering_plan_recovery import EngineeringPlanRecoveryBridge
from portable.engineering_plan_store import EngineeringPlanStore
from portable.engineering_recovery import EngineeringRecoveryLedger


WORK_KEY = "AUREN:issue-696:continuous-engineering"
HANDLER_NAME = "tests.test_agent_role_scope.role_scope_handler"
ROLE_SCOPE_HANDLER_CALLS: list[dict] = []
ROLE_SCOPE_AUTHORIZER_CALLS: list[tuple[str, str]] = []


def role_scope_handler(payload):
    ROLE_SCOPE_HANDLER_CALLS.append(dict(payload))
    return {"success": True, "detail": "role-scoped test complete", "evidence": ["test://role-scope/verified"]}


def allow_role_scope(task_id, scope):
    ROLE_SCOPE_AUTHORIZER_CALLS.append((task_id, scope.get("role_id", "")))
    return bool(task_id) and scope.get("delegation_evidence_ref") == "delegation://test/qa"


def deny_role_scope(_task_id, _scope):
    return False


def make_scope(**overrides):
    scope = {
        "schema_version": ROLE_SCOPE_SCHEMA_VERSION,
        "role_id": "QA-01",
        "manager_role_id": "EM-ENG-01",
        "action_class": "run_tests",
        "resource_scope": ["tests/"],
        "budget": {"max_attempts": 2, "max_duration_seconds": 300},
        "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=900)).isoformat(),
        "policy_digest": role_policy_digest(),
        "delegation_evidence_ref": "delegation://test/qa",
    }
    scope.update(overrides)
    return scope


def make_controller(root, *, authorizer=allow_role_scope, require_role_scope=True):
    controller = ExecutionController(
        root,
        role_scope_authorizer=authorizer,
        require_role_scope=require_role_scope,
    )
    controller.register_handler(HANDLER_NAME, role_scope_handler, execution_class="read_only")
    return controller


class AgentRoleScopeIntegrationTests(unittest.TestCase):
    def setUp(self):
        ROLE_SCOPE_HANDLER_CALLS.clear()
        ROLE_SCOPE_AUTHORIZER_CALLS.clear()

    def test_unknown_role_stale_policy_wrong_manager_and_resource_escape_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = make_controller(Path(tmp))
            invalid = [
                (make_scope(role_id="UNKNOWN-99"), {"path": "tests/test_agent_role_scope.py"}, "unknown role"),
                (make_scope(manager_role_id="CEO-01"), {"path": "tests/test_agent_role_scope.py"}, "manager"),
                (make_scope(policy_digest="0" * 64), {"path": "tests/test_agent_role_scope.py"}, "policy digest"),
                (make_scope(), {"path": "portable/execution_controller.py"}, "outside the delegated resource scope"),
                (make_scope(role_id="CTO-01", manager_role_id="CEO-01", action_class="waive_required_checks"),
                 {"path": "tests/test_agent_role_scope.py"}, "not permitted"),
            ]
            for scope, payload, message in invalid:
                with self.subTest(message=message):
                    with self.assertRaisesRegex(RoleScopeError, message):
                        controller.submit(HANDLER_NAME, payload, role_scope=scope)
            with self.assertRaisesRegex(RoleScopeError, "deadline exceeds"):
                controller.submit(
                    HANDLER_NAME, {"path": "tests/test_agent_role_scope.py"},
                    role_scope=make_scope(), deadline_seconds=301,
                )
            self.assertEqual(0, len(ROLE_SCOPE_HANDLER_CALLS))

    def test_role_scope_requires_a_host_authorizer_and_rejects_payload_forgery(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = ExecutionController(Path(tmp))
            controller.register_handler(HANDLER_NAME, role_scope_handler, execution_class="read_only")
            with self.assertRaisesRegex(RoleScopeError, "authorizer is unavailable"):
                controller.submit(
                    HANDLER_NAME, {"path": "tests/test_agent_role_scope.py"}, role_scope=make_scope(),
                )

            trusted = make_controller(Path(tmp) / "trusted")
            with self.assertRaisesRegex(ValueError, "reserved for trusted supervisor metadata"):
                trusted.submit(
                    HANDLER_NAME,
                    {"path": "tests/test_agent_role_scope.py", ROLE_SCOPE_KEY: make_scope()},
                )

    def test_missing_scope_and_independent_evaluator_cannot_execute_consequential_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = make_controller(Path(tmp))
            with self.assertRaisesRegex(RoleScopeError, "role scope is required"):
                controller.submit(HANDLER_NAME, {"path": "tests/test_agent_role_scope.py"})

        evaluator_scope = make_scope(
            role_id="EVAL-01",
            manager_role_id="independent_governance_council",
            action_class="evaluate_pinned_artifacts",
            approval_evidence_ref="approval://test/evaluator",
        )
        with self.assertRaisesRegex(RoleScopeError, "independent evaluator identity"):
            validate_role_scope(
                evaluator_scope,
                execution_class="consequential",
                payload={"path": "tests/test_agent_role_scope.py"},
                authorizer=lambda _task_id, _scope: True,
            )

    def test_role_scope_survives_restart_and_is_removed_from_handler_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = make_controller(root)
            task = controller.submit(
                HANDLER_NAME,
                {"path": "tests/test_agent_role_scope.py"},
                goal="Run QA-owned scoped tests",
                work_key=WORK_KEY,
                deadline_seconds=120,
                role_scope=make_scope(),
            )
            self.assertIsNotNone(task.deadline_at)
            del controller

            restarted = make_controller(root)
            restarted.run_once(task.id)
            self.assertEqual("completed", restarted.task(task.id).state)
            self.assertEqual(1, len(ROLE_SCOPE_HANDLER_CALLS))
            self.assertNotIn(ROLE_SCOPE_KEY, ROLE_SCOPE_HANDLER_CALLS[0])
            self.assertGreaterEqual(
                sum(1 for task_id, _role in ROLE_SCOPE_AUTHORIZER_CALLS if task_id == task.id), 2,
            )

    def test_changed_authority_after_restart_blocks_before_handler_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = make_controller(root)
            task = controller.submit(
                HANDLER_NAME,
                {"path": "tests/test_agent_role_scope.py"},
                goal="Run QA-owned scoped tests",
                work_key=WORK_KEY,
                role_scope=make_scope(),
            )
            del controller

            restarted = make_controller(root, authorizer=deny_role_scope)
            restarted.run_once(task.id)
            self.assertEqual("failed", restarted.task(task.id).state)
            self.assertEqual([], ROLE_SCOPE_HANDLER_CALLS)

    def test_plan_dispatch_binds_role_scope_to_canonical_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".auren"
            state_dir.mkdir(parents=True)
            store = EngineeringPlanStore(state_dir / "plans.sqlite3")
            plan = EngineeringPlan(
                run_id=WORK_KEY,
                goal="Run a role-scoped engineering task",
                requirements=("Role and resource scope must survive dispatch and restart.",),
                tasks=(PlanTask(
                    "tests", "Run assigned tests", ("Tests pass with retained evidence.",),
                    "Restore the previous test configuration",
                ),),
                source_revision="a" * 40,
                budget={"dispatches": 2},
            )
            run = new_plan_run(plan).transition_task(
                "tests", "ready", plan=plan, reason="task has no dependencies",
            )
            store.create(plan, run)
            ledger = EngineeringRecoveryLedger(state_dir / "recovery.sqlite3")
            recovery = EngineeringPlanRecoveryBridge(store=store, ledger=ledger)
            ledger.start_run(plan.run_id, recovery._intent(plan))
            ledger.checkpoint(
                plan.run_id, recovery.PHASE, status="in_progress",
                head_sha=plan.source_revision,
                payload=recovery._checkpoint_payload(
                    plan, run, next_safe_action="dispatch_role_scoped_tests",
                ),
            )
            packet = build_context_packet(
                plan=plan,
                run=run,
                task_id="tests",
                policy_constraints=("Do not expand delegated resource scope.",),
                budget_remaining={"attempts": 2, "dispatches": 2},
                next_safe_action="Run the assigned tests and retain evidence",
            )
            controller = make_controller(root)
            task_payload = {"path": "tests/test_agent_role_scope.py"}
            task = controller.dispatch_engineering_plan_task(
                store=store,
                run_id=WORK_KEY,
                task_id="tests",
                dispatch_key="role-scope-dispatch-1",
                handler=HANDLER_NAME,
                packet=packet,
                payload=task_payload,
                recovery_bridge=recovery,
                role_scope=make_scope(),
            )
            self.assertEqual("QA-01", task.payload[ROLE_SCOPE_KEY]["role_id"])
            self.assertIsNotNone(task.deadline_at)
            expected_payload = {**task_payload, ROLE_SCOPE_KEY: task.payload[ROLE_SCOPE_KEY]}
            expected_digest = hashlib.sha256(
                json.dumps(expected_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest()
            self.assertEqual(expected_digest, task.payload["auren_dispatch"]["payload_digest"])
            self.assertEqual("QA-01", controller._verify_role_scope(task)["role_id"])
            controller.run_once(task.id)
            self.assertEqual("completed", controller.task(task.id).state)
            self.assertEqual(1, len(ROLE_SCOPE_HANDLER_CALLS))
            self.assertNotIn(ROLE_SCOPE_KEY, ROLE_SCOPE_HANDLER_CALLS[0])


if __name__ == "__main__":
    unittest.main()
