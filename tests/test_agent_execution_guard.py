from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from portable.execution_controller import ExecutionController
from portable.guarded_filesystem import write_workspace_text

from portable.agent_execution_guard import (
    AgentAssignmentScopeDenied,
    validate_agent_assignment_for_dispatch,
)
from portable.agent_org_runtime import REGISTRY_PATH, authorize_agent_assignment
from portable.organization_guard import POLICY_VERSION, guard_policy_fingerprint


def assigned_filesystem_handler(payload):
    target = write_workspace_text(
        payload["workspace_root"], payload["path"], payload["content"]
    )
    return {"success": True, "evidence": ["filesystem-scope-test"], "target": str(target)}


class AgentExecutionGuardTests(unittest.TestCase):
    def _payload(self, scope=("portable/api.py",), action_class="edit_assigned_paths"):
        assignment = authorize_agent_assignment(
            role_id="BE-01",
            manager_id="EM-ENG-01",
            action_class=action_class,
            scope=list(scope),
            authorizer=lambda _request: True,
            registry_path=REGISTRY_PATH,
        )
        return {"path": scope[0], "_auren_agent_assignment": assignment.as_payload()}

    def _validate(self, payload, **kwargs):
        expected_policy_version = kwargs.pop("expected_policy_version", POLICY_VERSION)
        expected_policy_digest = kwargs.pop("expected_policy_digest", guard_policy_fingerprint(None))
        return validate_agent_assignment_for_dispatch(
            payload,
            expected_policy_version=expected_policy_version,
            expected_policy_digest=expected_policy_digest,
            **kwargs,
        )

    def test_assigned_resource_within_scope_passes(self):
        payload = self._payload()
        self._validate(
            payload, expected_action_class="edit_assigned_paths", resource=payload["path"],
            authorization_verifier=lambda _request: True,
        )

    def test_assigned_resource_outside_scope_fails_closed(self):
        payload = self._payload()
        with self.assertRaisesRegex(AgentAssignmentScopeDenied, "outside the assigned scope"):
            self._validate(
                payload, expected_action_class="edit_assigned_paths", resource="portable/secrets.py",
                authorization_verifier=lambda _request: True,
            )

    def test_missing_resource_fails_closed_for_assigned_task(self):
        payload = self._payload()
        with self.assertRaises(AgentAssignmentScopeDenied):
            self._validate(
                payload, expected_action_class="edit_assigned_paths", resource="",
                authorization_verifier=lambda _request: True,
            )

    def test_path_traversal_and_absolute_resource_are_denied(self):
        payload = self._payload()
        for path in ("../secrets.py", "/etc/passwd", "portable/../secrets.py", r"portable\secrets.py"):
            with self.subTest(path=path), self.assertRaises(AgentAssignmentScopeDenied):
                self._validate(
                    payload, expected_action_class="edit_assigned_paths", resource=path,
                    authorization_verifier=lambda _request: True,
                )

    def test_tampered_scope_digest_and_assignment_id_are_denied(self):
        for field, value in (("scope", ["portable/other.py"]), ("assignment_id", "forged")):
            payload = self._payload()
            payload["_auren_agent_assignment"][field] = value
            with self.subTest(field=field), self.assertRaises(AgentAssignmentScopeDenied):
                self._validate(
                    payload, expected_action_class="edit_assigned_paths", resource="portable/api.py",
                    authorization_verifier=lambda _request: True,
                )

    def test_stale_registry_digest_is_denied(self):
        payload = self._payload()
        payload["_auren_agent_assignment"]["registry_digest"] = "0" * 64
        with self.assertRaisesRegex(AgentAssignmentScopeDenied, "registry digest is stale"):
            self._validate(
                payload, expected_action_class="edit_assigned_paths", resource="portable/api.py", authorization_verifier=lambda _request: True
            )

    def test_malformed_reserved_envelope_is_denied(self):
        with self.assertRaises(AgentAssignmentScopeDenied):
            self._validate(
                {"_auren_agent_assignment": {"role_id": "BE-01"}}, resource="portable/api.py"
            )

    def test_assigned_task_requires_host_owned_verifier(self):
        payload = self._payload()
        with self.assertRaisesRegex(AgentAssignmentScopeDenied, "verifier is required"):
            self._validate(
                payload, expected_action_class="edit_assigned_paths", resource="portable/api.py",
            )

    def test_host_verifier_denial_and_exception_fail_closed(self):
        payload = self._payload()
        def broken(_request):
            raise RuntimeError("offline")
        for verifier in (lambda _request: False, lambda _request: 1, broken):
            with self.subTest(verifier=verifier), self.assertRaises(AgentAssignmentScopeDenied):
                self._validate(
                    payload, expected_action_class="edit_assigned_paths", resource="portable/api.py",
                    authorization_verifier=verifier,
                )

    def test_host_verifier_receives_immutable_digest_bound_assignment(self):
        payload = self._payload()
        seen = []
        def verifier(request):
            seen.append(request)
            with self.assertRaises(TypeError):
                request["scope"] = ("portable/other.py",)
            return True
        self._validate(
            payload, expected_action_class="edit_assigned_paths", resource="portable/api.py",
            authorization_verifier=verifier,
        )
        self.assertEqual(seen[0]["assignment_id"], payload["_auren_agent_assignment"]["assignment_id"])
        self.assertEqual(seen[0]["scope"], ("portable/api.py",))

    def test_unassigned_tasks_keep_existing_behavior(self):
        self._validate({"path": "portable/other.py"}, resource="portable/other.py")

    def test_scope_requires_exact_path_and_denies_descendants(self):
        payload = self._payload(("portable/api.py",))
        self._validate(
            payload, expected_action_class="edit_assigned_paths", resource="portable/api.py",
            authorization_verifier=lambda _request: True,
        )
        with self.assertRaises(AgentAssignmentScopeDenied):
            self._validate(
                payload, expected_action_class="edit_assigned_paths", resource="portable/api.py/routes.py",
                authorization_verifier=lambda _request: True,
            )

    def test_execution_controller_enforces_scope_inside_guarded_filesystem(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = ExecutionController(
                root, agent_assignment_verifier=lambda _request: True
            )
            controller.register_handler(
                "tests.test_agent_execution_guard.assigned_filesystem_handler",
                assigned_filesystem_handler,
                execution_class="read_only",
                agent_action_class="edit_assigned_paths",
            )
            assignment_payload = self._payload(("allowed.txt",))["_auren_agent_assignment"]
            blocked = controller.submit("tests.test_agent_execution_guard.assigned_filesystem_handler", {
                "resource": "allowed.txt",
                "path": "blocked.txt",
                "workspace_root": str(root),
                "content": "must not write",
                "_auren_agent_assignment": assignment_payload,
            })
            # Reconstruct the controller before dispatch: persisted role/scope
            # metadata must be revalidated, not trusted from the prior process.
            controller = ExecutionController(
                root, agent_assignment_verifier=lambda _request: True
            )
            with self.assertRaises(AgentAssignmentScopeDenied):
                controller.run_once(blocked.id)
            self.assertNotEqual(controller.task(blocked.id).state, "completed")
            self.assertFalse((root / "blocked.txt").exists())

            allowed = controller.submit("tests.test_agent_execution_guard.assigned_filesystem_handler", {
                "resource": "allowed.txt",
                "path": "allowed.txt",
                "workspace_root": str(root),
                "content": "allowed",
                "_auren_agent_assignment": assignment_payload,
            })
            controller.run_once(allowed.id)
            self.assertEqual(controller.task(allowed.id).state, "completed")
            self.assertEqual((root / "allowed.txt").read_text(encoding="utf-8"), "allowed")


    def test_assignment_action_class_must_match_registered_handler_before_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = ExecutionController(root, agent_assignment_verifier=lambda _request: True)
            controller.register_handler(
                "tests.test_agent_execution_guard.assigned_filesystem_handler",
                assigned_filesystem_handler,
                execution_class="read_only",
                agent_action_class="edit_assigned_paths",
            )
            assignment = self._payload(
                ("allowed.txt",), action_class="run_scoped_tests"
            )["_auren_agent_assignment"]
            with self.assertRaisesRegex(AgentAssignmentScopeDenied, "does not match the registered handler"):
                controller.submit(
                    "tests.test_agent_execution_guard.assigned_filesystem_handler",
                    {
                        "resource": "allowed.txt", "path": "allowed.txt",
                        "workspace_root": str(root), "content": "must not write",
                        "_auren_agent_assignment": assignment,
                    },
                )
            self.assertFalse((root / "allowed.txt").exists())

    def test_stale_policy_version_or_digest_fails_closed(self):
        payload = self._payload()
        with self.assertRaisesRegex(AgentAssignmentScopeDenied, "policy version is stale"):
            self._validate(
                payload, expected_policy_version="auren-guard-v0",
                resource="portable/api.py", authorization_verifier=lambda _request: True,
            )
        with self.assertRaisesRegex(AgentAssignmentScopeDenied, "policy digest is stale"):
            self._validate(
                payload, expected_policy_digest="0" * 64,
                resource="portable/api.py", authorization_verifier=lambda _request: True,
            )

    def test_stale_policy_change_is_rejected_before_assigned_task_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_dir = root / ".auren"
            policy_dir.mkdir()
            (policy_dir / "guard_policy.json").write_text(
                json.dumps({
                    "schema_version": POLICY_VERSION,
                    "default_effect": "deny",
                    "rules": [],
                }),
                encoding="utf-8",
            )
            controller = ExecutionController(root, agent_assignment_verifier=lambda _request: True)
            controller.register_handler(
                "tests.test_agent_execution_guard.assigned_filesystem_handler",
                assigned_filesystem_handler,
                execution_class="read_only",
                agent_action_class="edit_assigned_paths",
            )
            assignment = self._payload(("allowed.txt",))["_auren_agent_assignment"]
            with self.assertRaisesRegex(AgentAssignmentScopeDenied, "policy digest is stale"):
                controller.submit(
                    "tests.test_agent_execution_guard.assigned_filesystem_handler",
                    {
                        "resource": "allowed.txt",
                        "path": "allowed.txt",
                        "workspace_root": str(root),
                        "content": "must not write",
                        "_auren_agent_assignment": assignment,
                    },
                )
            self.assertFalse((root / "allowed.txt").exists())

    def test_assigned_work_without_handler_action_class_fails_before_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = ExecutionController(root, agent_assignment_verifier=lambda _request: True)
            controller.register_handler(
                "tests.test_agent_execution_guard.assigned_filesystem_handler",
                assigned_filesystem_handler,
                execution_class="read_only",
            )
            assignment = self._payload(("allowed.txt",))["_auren_agent_assignment"]
            with self.assertRaisesRegex(AgentAssignmentScopeDenied, "trusted handler action class"):
                controller.submit(
                    "tests.test_agent_execution_guard.assigned_filesystem_handler",
                    {
                        "resource": "allowed.txt", "path": "allowed.txt",
                        "workspace_root": str(root), "content": "must not write",
                        "_auren_agent_assignment": assignment,
                    },
                )
            with sqlite3.connect(controller.db_path) as db:
                count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            self.assertEqual(count, 0)
            self.assertFalse((root / "allowed.txt").exists())

    def test_handler_action_class_is_immutable_and_unknown_classes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = ExecutionController(root, agent_assignment_verifier=lambda _request: True)
            name = "tests.test_agent_execution_guard.assigned_filesystem_handler"
            controller.register_handler(
                name, assigned_filesystem_handler,
                execution_class="read_only", agent_action_class="edit_assigned_paths",
            )
            with self.assertRaisesRegex(ValueError, "immutable"):
                controller.register_handler(
                    name, assigned_filesystem_handler,
                    execution_class="read_only", agent_action_class="run_scoped_tests",
                )
            with self.assertRaisesRegex(ValueError, "not declared"):
                controller.register_handler(
                    "untrusted-agent-action", assigned_filesystem_handler,
                    execution_class="read_only", agent_action_class="waive_required_checks",
                )
            with self.assertRaisesRegex(ValueError, "delivery handlers"):
                controller.register_handler(
                    "safe-named-handler", assigned_filesystem_handler,
                    execution_class="read_only", guard_action="merge",
                    agent_action_class="review_verified_metrics",
                )

    def test_stale_assignment_policy_after_restart_blocks_before_handler_entry(self):
        import gc

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_dir = root / ".auren"
            policy_dir.mkdir()
            policy_path = policy_dir / "guard_policy.json"

            def write_policy(default_effect):
                policy_path.write_text(json.dumps({
                    "schema_version": POLICY_VERSION,
                    "default_effect": default_effect,
                    "rules": [],
                }), encoding="utf-8")

            write_policy("allow")
            handler_name = "tests.test_agent_execution_guard.assigned_filesystem_handler"
            controller = ExecutionController(
                root, agent_assignment_verifier=lambda _request: True
            )
            controller.register_handler(
                handler_name, assigned_filesystem_handler,
                execution_class="read_only", agent_action_class="edit_assigned_paths",
            )
            assignment = self._payload(("allowed.txt",))["_auren_agent_assignment"]
            task = controller.submit(handler_name, {
                "resource": "allowed.txt",
                "path": "allowed.txt",
                "workspace_root": str(root),
                "content": "must not write after stale policy",
                "_auren_agent_assignment": assignment,
            })
            del controller
            gc.collect()

            # The task and its assignment survive restart, but the effective
            # Guard policy changed while the task was pending.
            write_policy("deny")
            restarted = ExecutionController(
                root, agent_assignment_verifier=lambda _request: True
            )
            restarted.register_handler(
                handler_name, assigned_filesystem_handler,
                execution_class="read_only", agent_action_class="edit_assigned_paths",
            )
            restarted.run_once(task.id)

            self.assertNotEqual(restarted.task(task.id).state, "completed")
            self.assertNotEqual(restarted.task(task.id).state, "running")
            self.assertFalse((root / "allowed.txt").exists())

    def test_policy_change_after_dispatch_decision_receipt_is_rejected_before_handler_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_dir = root / ".auren"
            policy_dir.mkdir()
            policy_path = policy_dir / "guard_policy.json"

            def write_policy(default_effect):
                policy_path.write_text(json.dumps({
                    "schema_version": POLICY_VERSION,
                    "default_effect": default_effect,
                    "rules": [],
                }), encoding="utf-8")

            write_policy("allow")
            handler_name = "tests.test_agent_execution_guard.assigned_filesystem_handler"
            controller = ExecutionController(
                root, agent_assignment_verifier=lambda _request: True
            )
            controller.register_handler(
                handler_name, assigned_filesystem_handler,
                execution_class="read_only", agent_action_class="edit_assigned_paths",
            )
            assignment = self._payload(("allowed.txt",))["_auren_agent_assignment"]
            task = controller.submit(handler_name, {
                "resource": "allowed.txt",
                "path": "allowed.txt",
                "workspace_root": str(root),
                "content": "must not write after policy drift",
                "_auren_agent_assignment": assignment,
            })

            original_capture = controller._capture_event
            changed = False

            def change_policy_after_dispatch_receipt(event_type, *args, **kwargs):
                nonlocal changed
                receipt = original_capture(event_type, *args, **kwargs)
                if event_type == "guard_dispatch_decision" and not changed:
                    # Replace policy only after the allow decision is durably
                    # recorded; the final handler-boundary check must detect it.
                    changed = True
                    write_policy("deny")
                return receipt

            controller._capture_event = change_policy_after_dispatch_receipt
            with self.assertRaisesRegex(
                RuntimeError, "organization Guard could not be verified at the final handler boundary"
            ):
                controller.run_once(task.id)

            self.assertTrue(changed, "dispatch decision receipt was not persisted")
            self.assertNotEqual(controller.task(task.id).state, "completed")
            self.assertFalse((root / "allowed.txt").exists())

if __name__ == "__main__":
    unittest.main()
