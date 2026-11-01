from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from portable.organization_guard import POLICY_VERSION, guard_policy_fingerprint
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
        self.assertEqual(assignment.policy_version, POLICY_VERSION)
        self.assertEqual(assignment.policy_digest, guard_policy_fingerprint(None))
        self.assertEqual(calls[0]["policy_version"], POLICY_VERSION)
        self.assertEqual(calls[0]["policy_digest"], assignment.policy_digest)
        self.assertEqual(assignment.as_payload()["authorization"], "host_authorized_before_submission")

    def test_policy_file_is_bound_to_assignment_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "guard_policy.json"
            path.write_text(json.dumps({
                "schema_version": POLICY_VERSION,
                "default_effect": "deny",
                "rules": [],
            }), encoding="utf-8")
            assignment = authorize_agent_assignment(
                role_id="BE-01", manager_id="EM-ENG-01",
                action_class="edit_assigned_paths", scope=["portable/example.py"],
                authorizer=lambda _request: True, registry_path=REGISTRY, policy_path=path,
            )
            self.assertEqual(assignment.policy_digest, guard_policy_fingerprint({
                "schema_version": POLICY_VERSION, "default_effect": "deny", "rules": [],
            }))
            self.assertNotEqual(assignment.policy_digest, guard_policy_fingerprint(None))

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

    def test_only_roles_with_explicit_delegation_authority_can_assign_scoped_tasks(self):
        calls = []
        args = dict(
            action_class="manage_shared_budgets",
            scope=["portable/runtime.py"],
            authorizer=lambda request: calls.append(dict(request)) or True,
            registry_path=REGISTRY,
        )
        # CTO-01 is the configured parent of EM-ENG-01, but its role is
        # limited to technical direction and verification requests. It cannot
        # create worker assignments through the manager dispatch API.
        with self.assertRaisesRegex(AgentAssignmentDenied, "not authorized to delegate"):
            authorize_agent_assignment(
                role_id="EM-ENG-01", manager_id="CTO-01", **args
            )
        # CEO-01 is the configured parent of CTO-01, but portfolio priority
        # does not grant task-dispatch authority.
        with self.assertRaisesRegex(AgentAssignmentDenied, "not authorized to delegate"):
            authorize_agent_assignment(
                role_id="CTO-01", manager_id="CEO-01",
                action_class="request_verification", **{
                    key: value for key, value in args.items() if key != "action_class"
                }
            )
        self.assertEqual(calls, [], "unauthorized managers must be rejected before host authorization")

    def test_external_reporting_entity_cannot_act_as_assigning_manager(self):
        with self.assertRaisesRegex(AgentAssignmentDenied, "must be an organization role"):
            authorize_agent_assignment(
                role_id="CEO-01", manager_id="stakeholders_and_investors",
                action_class="prioritize_work", scope=["portfolio"],
                authorizer=lambda _request: True, registry_path=REGISTRY,
            )

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
        self.assertEqual(envelope["schema_version"], "auren-agent-assignment-v2")
        self.assertEqual(envelope["policy_version"], POLICY_VERSION)
        self.assertEqual(envelope["policy_digest"], guard_policy_fingerprint(None))
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
