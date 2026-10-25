from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from portable.execution_controller import ExecutionController
from portable.workflow_profile import WorkflowProfile, WorkflowProfileError


def workflow_profile_test_handler(payload):
    return {"success": True, "detail": "workflow profile test handler"}


class WorkflowProfileControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.root = Path(self._temporary_directory.name)
        self.controller = ExecutionController(self.root)
        self.controller.register_handler(
            "tests.workflow_profile_test_handler",
            workflow_profile_test_handler,
            execution_class="read_only",
        )

    def test_saved_profile_is_bound_to_task_and_submission_receipt(self) -> None:
        profile = WorkflowProfile(delivery_mode="stage_only", execution_mode="autonomous")
        self.controller.set_workflow_profile(profile)
        task = self.controller.submit(
            "tests.workflow_profile_test_handler",
            {"input": "safe"},
            work_key="workflow-profile-persisted",
        )
        self.assertEqual(profile.as_dict(), task.payload["auren_workflow_profile"])
        self.assertEqual(profile.digest, task.payload["auren_workflow_profile_digest"])
        with sqlite3.connect(self.controller.evidence_capture.path) as db:
            row = db.execute(
                "SELECT metadata_json FROM engineering_events WHERE task_id=? AND event_type='task_submitted'",
                (task.id,),
            ).fetchone()
        self.assertIsNotNone(row)
        metadata = json.loads(row[0])
        self.assertEqual(profile.digest, metadata["workflow_profile_digest"])
        self.assertEqual(profile.profile_version, metadata["workflow_profile_version"])

    def test_task_profile_overrides_project_and_saved_preferences(self) -> None:
        self.controller.set_workflow_profile(WorkflowProfile(delivery_mode="stage_only"))
        self.controller.set_workflow_profile(WorkflowProfile(delivery_mode="working_tree"), scope="project")
        task_profile = WorkflowProfile(delivery_mode="commit_each_change")
        task = self.controller.submit(
            "tests.workflow_profile_test_handler",
            {"auren_workflow_profile": task_profile.as_dict()},
        )
        self.assertEqual("commit_each_change", task.payload["auren_workflow_profile"]["delivery_mode"])

    def test_invalid_saved_profile_fails_closed_before_task_creation(self) -> None:
        self.controller.workflow_profile_path.write_text(
            '{"profile_version":"unsupported"}', encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            self.controller.submit("tests.workflow_profile_test_handler", {"input": "safe"})
        with sqlite3.connect(self.controller.db_path) as db:
            count = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        self.assertEqual(0, count)

    def test_resume_rejects_tampered_profile_digest_before_claim(self) -> None:
        profile = WorkflowProfile(delivery_mode="stage_only")
        self.controller.set_workflow_profile(profile)
        task = self.controller.submit("tests.workflow_profile_test_handler", {"input": "safe"})
        tampered = dict(task.payload)
        tampered["auren_workflow_profile_digest"] = "0" * 64
        with sqlite3.connect(self.controller.db_path) as db:
            db.execute(
                "UPDATE tasks SET payload=? WHERE id=?",
                (json.dumps(tampered, sort_keys=True, separators=(",", ":")), task.id),
            )
        with self.assertRaisesRegex(WorkflowProfileError, "digest mismatch"):
            self.controller.run_once(task.id)
        self.assertEqual("pending", self.controller.task(task.id).state)

    def test_unconfigured_legacy_submission_keeps_its_original_intent_shape(self) -> None:
        task = self.controller.submit("tests.workflow_profile_test_handler", {"input": "legacy"})
        self.assertNotIn("auren_workflow_profile", task.payload)
        self.assertNotIn("auren_workflow_profile_digest", task.payload)


if __name__ == "__main__":
    unittest.main()
