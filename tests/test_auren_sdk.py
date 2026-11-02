"""Public SDK contract tests."""
from __future__ import annotations

import tempfile
import unittest

from portable.sdk import AurenClient, SDK_API_VERSION
from portable.agent_org_runtime import REGISTRY_PATH, authorize_agent_assignment


def sdk_test_handler(payload):
    return {"accepted": True, "value": payload.get("value")}


def sdk_assigned_handler(payload):
    return {"success": True, "evidence": ["sdk-assignment-verification"]}


class AurenClientTests(unittest.TestCase):
    def test_api_version_and_canonical_durable_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = AurenClient(tmp, dashboard_enabled=False)
            try:
                self.assertEqual("v1", SDK_API_VERSION)
                client.register_handler("sdk-test", sdk_test_handler, execution_class="read_only")
                task = client.submit("sdk-test", {"value": "bounded"})
                persisted = client.task(task.id)
                self.assertIsNotNone(persisted)
                self.assertEqual(task.id, persisted.id)
                self.assertEqual("pending", persisted.state)
                self.assertEqual("queued", client.progress(task.id)["state"])
            finally:
                client.close()

    def test_assignment_verifier_is_plumbed_through_sdk_to_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            verified = []
            def verifier(request):
                verified.append(request)
                return True
            client = AurenClient(tmp, dashboard_enabled=False, agent_assignment_verifier=verifier)
            try:
                client.register_handler(
                    "sdk-assigned", sdk_assigned_handler, execution_class="read_only",
                    agent_action_class="edit_assigned_paths",
                )
                assignment = authorize_agent_assignment(
                    role_id="BE-01", manager_id="EM-ENG-01",
                    action_class="edit_assigned_paths", scope=["allowed.txt"],
                    work_key="AUREN:test:sdk-assigned",
                    task_payload={"resource": "allowed.txt", "path": "allowed.txt"},
                    authorizer=lambda _request: True, registry_path=REGISTRY_PATH,
                )
                task = client.submit("sdk-assigned", {
                    "resource": "allowed.txt", "path": "allowed.txt",
                    "_auren_agent_assignment": assignment.as_payload(),
                }, work_key="AUREN:test:sdk-assigned")
                client.run_once(task.id)
                self.assertEqual("completed", client.task(task.id).state)
                self.assertGreaterEqual(len(verified), 2)
                self.assertEqual("auren-agent-assignment-v3", verified[0]["schema_version"])
            finally:
                client.close()

    def test_sdk_assignment_entrypoint_runs_authorizer_and_dispatch_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            authorized, verified = [], []
            client = AurenClient(
                tmp, dashboard_enabled=False,
                agent_assignment_verifier=lambda request: verified.append(request) or True,
            )
            try:
                client.register_handler(
                    "sdk-assigned", sdk_assigned_handler, execution_class="read_only",
                    agent_action_class="edit_assigned_paths",
                )
                task = client.submit_agent_task(
                    "sdk-assigned", {"resource": "allowed.txt", "path": "allowed.txt"},
                    role_id="BE-01", manager_id="EM-ENG-01",
                    action_class="edit_assigned_paths", scope=["allowed.txt"],
                    authorizer=lambda request: authorized.append(dict(request)) or True,
                    work_key="AUREN:test:sdk-entrypoint", deadline_seconds=30,
                    registry_path=REGISTRY_PATH,
                )
                self.assertEqual("pending", client.task(task.id).state)
                self.assertEqual(1, len(authorized))
                self.assertEqual("AUREN:test:sdk-entrypoint", authorized[0]["work_key"])
                client.run_once(task.id)
                self.assertEqual("completed", client.task(task.id).state)
                self.assertTrue(verified)
                self.assertEqual(authorized[0]["assignment_digest"], verified[-1]["assignment_digest"])
            finally:
                client.close()

    def test_sdk_assignment_entrypoint_rejects_caller_forged_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = AurenClient(
                tmp, dashboard_enabled=False,
                agent_assignment_verifier=lambda _request: True,
            )
            try:
                client.register_handler(
                    "sdk-assigned", sdk_assigned_handler, execution_class="read_only",
                    agent_action_class="edit_assigned_paths",
                )
                with self.assertRaisesRegex(ValueError, "reserved"):
                    client.submit_agent_task(
                        "sdk-assigned", {"_auren_agent_assignment": {"role_id": "CEO-01"}},
                        role_id="BE-01", manager_id="EM-ENG-01",
                        action_class="edit_assigned_paths", scope=["allowed.txt"],
                        authorizer=lambda _request: True,
                        work_key="AUREN:test:sdk-forged", registry_path=REGISTRY_PATH,
                    )
            finally:
                client.close()

    def test_sdk_assignment_without_dispatch_verifier_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            invoked = []
            client = AurenClient(tmp, dashboard_enabled=False)
            try:
                client.register_handler(
                    "sdk-assigned",
                    lambda payload: invoked.append(payload) or sdk_assigned_handler(payload),
                    execution_class="read_only", agent_action_class="edit_assigned_paths",
                )
                task = client.submit_agent_task(
                    "sdk-assigned", {"resource": "allowed.txt", "path": "allowed.txt"},
                    role_id="BE-01", manager_id="EM-ENG-01",
                    action_class="edit_assigned_paths", scope=["allowed.txt"],
                    authorizer=lambda _request: True,
                    work_key="AUREN:test:sdk-no-verifier", registry_path=REGISTRY_PATH,
                )
                client.run_once(task.id)
                self.assertNotEqual("completed", client.task(task.id).state)
                self.assertEqual([], invoked)
            finally:
                client.close()

    def test_unknown_execution_class_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = AurenClient(tmp, dashboard_enabled=False)
            try:
                with self.assertRaisesRegex(ValueError, "execution_class"):
                    client.register_handler("sdk-test", sdk_test_handler, execution_class="untrusted")
            finally:
                client.close()

    def test_dashboard_is_optional_and_observational(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = AurenClient(tmp, dashboard_enabled=False)
            try:
                with self.assertRaisesRegex(RuntimeError, "disabled"):
                    client.dashboard_snapshot()
            finally:
                client.close()

    def test_memory_facade_uses_shared_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = AurenClient(tmp, dashboard_enabled=False)
            try:
                self.assertIsNone(client.remember("repo-a", "semantic", "keep verified decisions"))
                saved = client.remember(
                    "repo-a", "semantic", "keep verified decisions", approved=True
                )
                self.assertIsNotNone(saved)
                records = client.search_memory("repo-a", "verified decisions")
                self.assertTrue(records)
                self.assertIn("verified decisions", records[0].text)
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
