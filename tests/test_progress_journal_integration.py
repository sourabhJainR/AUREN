import tempfile
import unittest
from pathlib import Path

from portable.engineering_dashboard import EngineeringDashboard
from portable.execution_controller import ExecutionController
from portable.progress_journal import ProgressJournal


def progress_success_handler(payload):
    return {"success": True, "detail": "verified fixture completion"}


class ProgressJournalRuntimeIntegrationTests(unittest.TestCase):
    def test_supervisor_lifecycle_and_dashboard_share_durable_progress(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller = ExecutionController(root)
            controller.register_handler(
                "tests.progress_success", progress_success_handler, execution_class="read_only"
            )
            task = controller.submit("tests.progress_success", {"request_id": "safe-fixture"})
            journal = ProgressJournal(controller.db_path.parent / "progress.jsonl")

            queued = journal.snapshot(task.id)
            self.assertEqual(queued["state"], "queued")
            self.assertEqual(queued["sequence"], 1)

            controller.run_once(task.id)

            completed = journal.snapshot(task.id)
            self.assertEqual(completed["state"], "completed")
            self.assertGreaterEqual(completed["event_count"], 6)
            events = [row["event"] for row in journal._events() if row["task_id"] == task.id]
            self.assertIn("preflight_started", events)
            self.assertIn("handler_started", events)
            self.assertIn("task_terminal", events)

            dashboard = EngineeringDashboard(root)
            rows = dashboard._progress_snapshots()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["task_id"], task.id)
            self.assertEqual(rows[0]["state"], "completed")

    def test_journal_failure_is_visible_without_falsifying_task_state(self):
        with tempfile.TemporaryDirectory() as temp:
            controller = ExecutionController(temp)
            controller.register_handler(
                "tests.progress_success", progress_success_handler, execution_class="read_only"
            )

            def fail_write(*args, **kwargs):
                raise OSError("controlled journal outage")

            controller.progress_journal.record = fail_write
            task = controller.submit("tests.progress_success", {"request_id": "safe-fixture"})
            self.assertEqual(controller.task(task.id).state, "pending")
            self.assertTrue(any("progress_journal_write_failed" in finding
                                for finding in controller.health_findings()))


if __name__ == "__main__":
    unittest.main()
