from __future__ import annotations

from dataclasses import replace
import sqlite3
from pathlib import Path
import tempfile
import unittest

from portable.execution_controller import ExecutionController
from portable.workflow_profile import WorkflowProfile
from portable.workflow_profile_store import WorkflowProfileStoreError, save_profile


_WORKFLOW_PROFILE_HANDLER_CALLS = []


def workflow_profile_test_handler(payload):
    _WORKFLOW_PROFILE_HANDLER_CALLS.append(dict(payload))
    return {"ok": True}


class WorkflowProfileSupervisorBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.root = Path(self._temporary_directory.name)
        self.controller = ExecutionController(self.root)
        _WORKFLOW_PROFILE_HANDLER_CALLS.clear()
        self.controller.register_handler(
            "workflow-profile-test",
            workflow_profile_test_handler,
            execution_class="read_only",
        )

    def test_safe_default_profile_is_bound_to_new_durable_task(self) -> None:
        task = self.controller.submit(
            "workflow-profile-test", {"value": 1}, work_key="workflow-profile:default",
        )
        profile = WorkflowProfile.from_mapping(task.payload["_auren_workflow_profile"])
        self.assertEqual(WorkflowProfile(), profile)
        self.assertEqual(profile.digest, task.payload["_auren_workflow_profile_digest"])
        self.assertNotIn("workflow_profile", task.payload)

    def test_task_profile_overrides_saved_profile_but_cannot_spoof_internal_binding(self) -> None:
        save_profile(
            self.controller.workflow_profile_path,
            WorkflowProfile(delivery_mode="stage_only", execution_mode="autonomous"),
        )
        task_profile = WorkflowProfile(delivery_mode="working_tree", execution_mode="bounded_batches")
        task = self.controller.submit(
            "workflow-profile-test",
            {
                "value": 2,
                "workflow_profile": task_profile.as_dict(),
                "_auren_workflow_profile": {"profile_version": "attacker"},
                "_auren_workflow_profile_digest": "0" * 64,
            },
            work_key="workflow-profile:task-override",
        )
        self.assertEqual(task_profile.as_dict(), task.payload["_auren_workflow_profile"])
        self.assertEqual(task_profile.digest, task.payload["_auren_workflow_profile_digest"])
        self.assertNotIn("workflow_profile", task.payload)

    def test_corrupt_saved_profile_fails_closed_before_task_submission(self) -> None:
        self.controller.workflow_profile_path.write_text('{"profile_version":"unknown"}', encoding="utf-8")
        with self.assertRaises(WorkflowProfileStoreError):
            self.controller.submit(
                "workflow-profile-test", {"value": 3}, work_key="workflow-profile:corrupt-saved",
            )

    def test_invalid_task_profile_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            self.controller.submit(
                "workflow-profile-test",
                {"workflow_profile": {"profile_version": "unsupported"}},
                work_key="workflow-profile:invalid-task",
            )

    def test_profile_binding_tampering_is_rejected_before_dispatch(self) -> None:
        task = self.controller.submit(
            "workflow-profile-test", {"value": 4}, work_key="workflow-profile:tamper",
        )
        tampered = replace(
            task,
            payload={**task.payload, "_auren_workflow_profile_digest": "0" * 64},
        )
        with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
            self.controller._verify_workflow_profile_binding(tampered)

    def test_stage_only_profile_blocks_merge_intent_before_handler_dispatch(self) -> None:
        task = self.controller.submit(
            "workflow-profile-test",
            {
                "value": 6,
                "operation": "merge",
                "workflow_profile": WorkflowProfile(
                    delivery_mode="stage_only", execution_mode="autonomous",
                ).as_dict(),
            },
            work_key="workflow-profile:stage-only-merge",
        )
        self.controller.run_once(task.id)
        self.assertEqual("failed", self.controller.task(task.id).state)
        self.assertEqual([], _WORKFLOW_PROFILE_HANDLER_CALLS)

    def test_stage_only_blocks_generic_consequential_handler_before_dispatch(self) -> None:
        task = self.controller.submit(
            "workflow-profile-test",
            {
                "value": 7,
                "workflow_profile": WorkflowProfile(
                    delivery_mode="stage_only", execution_mode="autonomous",
                ).as_dict(),
            },
            work_key="workflow-profile:stage-only-consequential",
        )
        # Simulate a previously registered generic handler classified as
        # consequential; no action name or task field is needed to trigger the
        # fail-closed stage-only boundary.
        db = sqlite3.connect(self.controller.db_path)
        try:
            with db:
                db.execute(
                    "UPDATE handlers SET execution_class='consequential' WHERE name=?",
                    ("workflow-profile-test",),
                )
        finally:
            db.close()
        self.controller.run_once(task.id)
        self.assertEqual("failed", self.controller.task(task.id).state)
        self.assertEqual([], _WORKFLOW_PROFILE_HANDLER_CALLS)

    def test_saved_profile_change_cannot_reuse_existing_work_key(self) -> None:
        save_profile(
            self.controller.workflow_profile_path,
            WorkflowProfile(delivery_mode="stage_only"),
        )
        self.controller.submit(
            "workflow-profile-test", {"value": 5}, work_key="workflow-profile:stable-key",
        )
        save_profile(
            self.controller.workflow_profile_path,
            WorkflowProfile(delivery_mode="working_tree"),
        )
        with self.assertRaisesRegex(ValueError, "different handler, payload, or goal"):
            self.controller.submit(
                "workflow-profile-test", {"value": 5}, work_key="workflow-profile:stable-key",
            )


if __name__ == "__main__":
    unittest.main()
