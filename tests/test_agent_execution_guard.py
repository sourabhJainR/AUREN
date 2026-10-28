from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from portable.agent_execution_guard import (
    AgentAssignmentScopeDenied,
    validate_agent_assignment_for_dispatch,
)
from portable.agent_org_runtime import REGISTRY_PATH, authorize_agent_assignment


class AgentExecutionGuardTests(unittest.TestCase):
    def _payload(self, scope=("portable/api.py",)):
        assignment = authorize_agent_assignment(
            role_id="BE-01",
            manager_id="EM-ENG-01",
            action_class="edit_assigned_paths",
            scope=list(scope),
            authorizer=lambda _request: True,
            registry_path=REGISTRY_PATH,
        )
        return {"path": scope[0], "_auren_agent_assignment": assignment.as_payload()}

    def test_assigned_resource_within_scope_passes(self):
        payload = self._payload()
        validate_agent_assignment_for_dispatch(payload, resource=payload["path"])

    def test_assigned_resource_outside_scope_fails_closed(self):
        payload = self._payload()
        with self.assertRaisesRegex(AgentAssignmentScopeDenied, "outside the assigned scope"):
            validate_agent_assignment_for_dispatch(payload, resource="portable/secrets.py")

    def test_missing_resource_fails_closed_for_assigned_task(self):
        payload = self._payload()
        with self.assertRaises(AgentAssignmentScopeDenied):
            validate_agent_assignment_for_dispatch(payload, resource="")

    def test_path_traversal_and_absolute_resource_are_denied(self):
        payload = self._payload()
        for path in ("../secrets.py", "/etc/passwd", "portable/../secrets.py", r"portable\secrets.py"):
            with self.subTest(path=path), self.assertRaises(AgentAssignmentScopeDenied):
                validate_agent_assignment_for_dispatch(payload, resource=path)

    def test_tampered_scope_digest_and_assignment_id_are_denied(self):
        for field, value in (("scope", ["portable/other.py"]), ("assignment_id", "forged")):
            payload = self._payload()
            payload["_auren_agent_assignment"][field] = value
            with self.subTest(field=field), self.assertRaises(AgentAssignmentScopeDenied):
                validate_agent_assignment_for_dispatch(payload, resource="portable/api.py")

    def test_stale_registry_digest_is_denied(self):
        payload = self._payload()
        payload["_auren_agent_assignment"]["registry_digest"] = "0" * 64
        with self.assertRaisesRegex(AgentAssignmentScopeDenied, "registry digest is stale"):
            validate_agent_assignment_for_dispatch(payload, resource="portable/api.py")

    def test_malformed_reserved_envelope_is_denied(self):
        with self.assertRaises(AgentAssignmentScopeDenied):
            validate_agent_assignment_for_dispatch(
                {"_auren_agent_assignment": {"role_id": "BE-01"}}, resource="portable/api.py"
            )

    def test_unassigned_tasks_keep_existing_behavior(self):
        validate_agent_assignment_for_dispatch({"path": "portable/other.py"}, resource="portable/other.py")

    def test_scope_requires_exact_path_and_denies_descendants(self):
        payload = self._payload(("portable/api.py",))
        validate_agent_assignment_for_dispatch(payload, resource="portable/api.py")
        with self.assertRaises(AgentAssignmentScopeDenied):
            validate_agent_assignment_for_dispatch(payload, resource="portable/api.py/routes.py")


if __name__ == "__main__":
    unittest.main()
