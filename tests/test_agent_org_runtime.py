from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from portable.agent_org_runtime import (
    AgentAssignmentDenied,
    authorize_agent_assignment,
    load_agent_org_registry,
    submit_agent_task,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "agent_org.v1.json"


class _Controller:
    def __init__(self):
        self.submissions = []

    def submit(self, handler, payload, **kwargs):
        result = {"handler": handler, "payload": payload, **kwargs}
        self.submissions.append(result)
        return result


class AgentOrgRuntimeTests(unittest.TestCase):
    def test_load_registry_checks_digest_and_role_graph(self):
        registry, digest = load_agent_org_registry(REGISTRY)
        self.assertEqual(registry["schema_version"], "auren-ai-engineering-org-v1")
        self.assertEqual(len(digest), 64)
        self.assertIn("BE-01", {role["id"] for role in registry["roles"]})

    def test_authorized_assignment_is_bound_to_role_manager_action_and_scope(self):
        calls = []
        assignment = authorize_agent_assignment(
            role_id="BE-01",
            manager_id="EM-ENG-01",
            action_class="edit_assigned_paths",
            scope=["portable/example.py", "tests/test_example.py"],
            authorizer=lambda request: calls.append(dict(request)) or True,
            registry_path=REGISTRY,
        )
        self.assertEqual(assignment.role_id, "BE-01")
        self.assertEqual(assignment.manager_id, "EM-ENG-01")
        self.assertEqual(assignment.scope, ("portable/example.py", "tests/test_example.py"))
        self.assertEqual(assignment.assignment_digest, calls[0]["assignment_digest"])
        self.assertEqual(assignment.as_payload()["authorization"], "host_authorized_before_submission")

    def test_authorizer_cannot_mutate_the_assignment_after_digest_calculation(self):
        def attempted_mutation(request):
            with self.assertRaises(TypeError):
                request["scope"] = ("portable/unauthorized.py",)
            with self.assertRaises(AttributeError):
                request["scope"].append("portable/unauthorized.py")
            return True
        assignment = authorize_agent_assignment(
            role_id="BE-01", manager_id="EM-ENG-01", action_class="edit_assigned_paths",
            scope=["portable/allowed.py"], authorizer=attempted_mutation, registry_path=REGISTRY,
        )
        self.assertEqual(("portable/allowed.py",), assignment.scope)

    def test_wrong_manager_unknown_role_and_unpermitted_action_fail_closed(self):
        base = dict(
            role_id="BE-01", manager_id="CTO-01", action_class="edit_assigned_paths",
            scope=["portable/example.py"], authorizer=lambda _request: True,
            registry_path=REGISTRY,
        )
        with self.assertRaises(AgentAssignmentDenied):
            authorize_agent_assignment(**base)
        base["role_id"] = "NO-SUCH-ROLE"
        base["manager_id"] = "EM-ENG-01"
        with self.assertRaises(AgentAssignmentDenied):
            authorize_agent_assignment(**base)
        base["role_id"] = "BE-01"
        base["action_class"] = "override_host_policy"
        with self.assertRaises(AgentAssignmentDenied):
            authorize_agent_assignment(**base)

    def test_host_authorizer_must_return_literal_true_and_exceptions_fail_closed(self):
        args = dict(
            role_id="BE-01", manager_id="EM-ENG-01", action_class="edit_assigned_paths",
            scope=["portable/example.py"], registry_path=REGISTRY,
        )
        for result in (False, None, 1, "true", {"authorized": True}):
            with self.subTest(result=result), self.assertRaises(AgentAssignmentDenied):
                authorize_agent_assignment(**args, authorizer=lambda _request, result=result: result)
        def broken(_request):
            raise RuntimeError("identity service unavailable")
        with self.assertRaises(AgentAssignmentDenied):
            authorize_agent_assignment(**args, authorizer=broken)

    def test_assignment_scope_is_bounded_and_cannot_be_duplicated(self):
        args = dict(
            role_id="BE-01", manager_id="EM-ENG-01", action_class="edit_assigned_paths",
            authorizer=lambda _request: True, registry_path=REGISTRY,
        )
        with self.assertRaises(ValueError):
            authorize_agent_assignment(**args, scope=[])
        with self.assertRaises(ValueError):
            authorize_agent_assignment(**args, scope=["portable/a.py", "portable/a.py"])
        with self.assertRaises(ValueError):
            authorize_agent_assignment(**args, scope=["x" * 513])

    def test_submit_persists_authorized_assignment_in_durable_task_payload(self):
        controller = _Controller()
        result = submit_agent_task(
            controller, "backend_handler", {"task": "edit API contract"},
            role_id="BE-01", manager_id="EM-ENG-01",
            action_class="edit_assigned_paths",
            scope=["portable/api.py"], authorizer=lambda _request: True,
            goal="Implement scoped API change", work_key="AUREN:test:agent-assignment",
            deadline_seconds=30, registry_path=REGISTRY,
        )
        self.assertIs(result, controller.submissions[0])
        envelope = result["payload"]["_auren_agent_assignment"]
        self.assertEqual(envelope["role_id"], "BE-01")
        self.assertEqual(envelope["scope"], ["portable/api.py"])
        self.assertEqual(envelope["schema_version"], "auren-agent-assignment-v1")
        self.assertEqual(result["work_key"], "AUREN:test:agent-assignment")
        self.assertEqual(result["deadline_seconds"], 30)

    def test_caller_cannot_overwrite_reserved_assignment_metadata(self):
        with self.assertRaises(ValueError):
            submit_agent_task(
                _Controller(), "backend_handler",
                {"_auren_agent_assignment": {"role_id": "CEO-01"}},
                role_id="BE-01", manager_id="EM-ENG-01",
                action_class="edit_assigned_paths", scope=["portable/api.py"],
                authorizer=lambda _request: True, registry_path=REGISTRY,
            )

    def test_duplicate_json_fields_in_registry_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "registry.json"
            path.write_text('{"schema_version":"auren-ai-engineering-org-v1","schema_version":"bad","roles":[],"external_entities":[]}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_agent_org_registry(path)


if __name__ == "__main__":
    unittest.main()
