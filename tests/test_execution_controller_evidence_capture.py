"""ExecutionController records lifecycle evidence without copying task payloads."""
import tempfile
import unittest
from pathlib import Path

from portable.execution_controller import ExecutionController


def successful_work_handler(payload):
    return {"success": True, "detail": "completed", "evidence": ["unit-test"]}


def failed_work_handler(_payload):
    return {"success": False, "detail": "regression failed"}


class ExecutionControllerEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = ExecutionController(Path(self.temp.name))

    def test_task_lifecycle_and_attempt_number_are_captured_automatically(self):
        self.controller.register_handler("compute", successful_work_handler, execution_class="read_only")
        task = self.controller.submit("compute", {"prompt": "DO-NOT-STORE", "api_key": "secret"})
        self.controller.run_once(task.id)
        capture = self.controller.evidence_capture
        events = capture.events(task_id=task.id)
        self.assertEqual([event["event_type"] for event in events], [
            "task_submitted", "task_attempt_started", "guard_dispatch_decision", "task_completed",
        ])
        self.assertEqual(events[1]["iteration"], 1)
        serialized = repr(events)
        self.assertNotIn("DO-NOT-STORE", serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("secret", serialized)
        self.assertTrue(capture.verify_chain())

    def test_failed_work_is_preserved_as_negative_evidence(self):
        self.controller.register_handler("compute", failed_work_handler, execution_class="read_only")
        task = self.controller.submit("compute", {"safe": "metadata"})
        self.controller.run_once(task.id)
        events = self.controller.evidence_capture.events(task_id=task.id)
        self.assertEqual(events[-1]["event_type"], "task_failed")
        self.assertEqual(events[-1]["status"], "failed")
        self.assertEqual(events[-1]["metadata"]["result_category"], "failure")


if __name__ == "__main__":
    unittest.main()
