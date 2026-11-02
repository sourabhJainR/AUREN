from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.agent_execution_guard import (
    AgentAssignmentScopeDenied,
    agent_assignment_scope,
)
from portable.agent_org_runtime import (
    REGISTRY_PATH,
    AgentAssignmentDenied,
    authorize_agent_assignment,
    submit_agent_task,
)
from portable.execution_controller import ExecutionController


_NESTED_ASSIGNMENT_RESULTS: list[str] = []
_NESTED_HOST_AUTHORIZER_CALLS: list[object] = []


def nested_delegation_probe(payload):
    try:
        authorize_agent_assignment(
            role_id="QA-01",
            manager_id="EM-ENG-01",
            action_class="run_tests",
            scope=[payload["resource"]],
            authorizer=lambda request: _NESTED_HOST_AUTHORIZER_CALLS.append(request) or True,
            work_key="AUREN:test:nested-delegation-probe",
            registry_path=REGISTRY_PATH,
        )
    except AgentAssignmentDenied as exc:
        _NESTED_ASSIGNMENT_RESULTS.append(str(exc))
        return {"success": True, "evidence": ["nested-delegation-denied"]}
    _NESTED_ASSIGNMENT_RESULTS.append("unexpectedly-authorized")
    return {"success": True, "evidence": ["unexpected-nested-authorization"]}


class DelegatedAgentIdentityTests(unittest.TestCase):
    def test_nested_assignment_must_use_the_active_agent_as_manager(self):
        host_calls = []
        with agent_assignment_scope(("portable/allowed.py",), role_id="BE-01"):
            with self.assertRaisesRegex(
                AgentAssignmentDenied, "manager must match the active assigned agent role"
            ):
                authorize_agent_assignment(
                    role_id="QA-01",
                    manager_id="EM-ENG-01",
                    action_class="run_tests",
                    scope=["portable/allowed.py"],
                    authorizer=lambda request: host_calls.append(request) or True,
                    work_key="AUREN:test:nested-manager-spoof",
                    registry_path=REGISTRY_PATH,
                )
        self.assertEqual([], host_calls, "spoofed manager identity must be denied before host authorization")

    def test_nested_assignment_scope_must_be_a_subset_of_parent_scope(self):
        host_calls = []
        with agent_assignment_scope(("portable/allowed.py",), role_id="EM-ENG-01"):
            with self.assertRaisesRegex(
                AgentAssignmentDenied, "scope cannot exceed the active manager assignment"
            ):
                authorize_agent_assignment(
                    role_id="BE-01",
                    manager_id="EM-ENG-01",
                    action_class="edit_assigned_paths",
                    scope=["portable/secrets.py"],
                    authorizer=lambda request: host_calls.append(request) or True,
                    work_key="AUREN:test:nested-scope-expansion",
                    registry_path=REGISTRY_PATH,
                )
        self.assertEqual([], host_calls, "scope expansion must be denied before host authorization")

    def test_nested_assignment_within_parent_scope_is_allowed(self):
        host_calls = []
        with agent_assignment_scope(
            ("portable/allowed.py", "tests/allowed.py"), role_id="EM-ENG-01"
        ):
            assignment = authorize_agent_assignment(
                role_id="BE-01",
                manager_id="EM-ENG-01",
                action_class="edit_assigned_paths",
                scope=["portable/allowed.py"],
                authorizer=lambda request: host_calls.append(request) or True,
                work_key="AUREN:test:nested-scope-subset",
                registry_path=REGISTRY_PATH,
            )
        self.assertEqual(("portable/allowed.py",), assignment.scope)
        self.assertEqual(1, len(host_calls))

    def test_assigned_handler_cannot_replace_its_active_scope_or_role(self):
        with agent_assignment_scope(("portable/allowed.py",), role_id="BE-01"):
            with self.assertRaisesRegex(AgentAssignmentScopeDenied, "cannot replace"):
                with agent_assignment_scope(("portable/secrets.py",), role_id="EM-ENG-01"):
                    self.fail("a handler must not replace its active assignment context")

    def test_execution_controller_binds_role_identity_during_handler_dispatch(self):
        _NESTED_ASSIGNMENT_RESULTS.clear()
        _NESTED_HOST_AUTHORIZER_CALLS.clear()
        with tempfile.TemporaryDirectory() as directory:
            controller = ExecutionController(
                Path(directory), agent_assignment_verifier=lambda _request: True,
            )
            handler = "tests.test_agent_delegation_chain.nested_delegation_probe"
            controller.register_handler(
                handler,
                nested_delegation_probe,
                execution_class="read_only",
                agent_action_class="edit_assigned_paths",
            )
            resource = "portable/allowed.py"
            task = submit_agent_task(
                controller,
                handler,
                {"resource": resource, "path": resource},
                role_id="BE-01",
                manager_id="EM-ENG-01",
                action_class="edit_assigned_paths",
                scope=[resource],
                authorizer=lambda _request: True,
                work_key="AUREN:test:controller-role-context",
                registry_path=REGISTRY_PATH,
            )

            controller.run_once(task.id)

            self.assertEqual("completed", controller.task(task.id).state)
            self.assertEqual(1, len(_NESTED_ASSIGNMENT_RESULTS))
            self.assertIn("manager must match the active assigned agent role", _NESTED_ASSIGNMENT_RESULTS[0])
            self.assertEqual([], _NESTED_HOST_AUTHORIZER_CALLS)


if __name__ == "__main__":
    unittest.main()
