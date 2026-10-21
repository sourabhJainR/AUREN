import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
            payload = dashboard.snapshot().as_dict()
            rows = payload["tasks"]["progress"]
            matching = [row for row in rows if row["task_id"] == task.id]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]["state"], "completed")

    def test_progress_never_claims_completion_before_work_unit_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            controller = ExecutionController(temp)
            controller.register_handler(
                "tests.progress_success", progress_success_handler, execution_class="read_only"
            )
            task = controller.submit("tests.progress_success", {"request_id": "safe-fixture"})

            def fail_finish(*args, **kwargs):
                raise OSError("controlled receipt store failure")

            controller.company.finish = fail_finish
            with self.assertRaisesRegex(OSError, "controlled receipt store failure"):
                controller.run_once(task.id)

            snapshot = ProgressJournal(controller.db_path.parent / "progress.jsonl").snapshot(
                task.id, stale_after_seconds=3600
            )
            self.assertEqual(snapshot["reported_state"], "running")
            self.assertEqual(snapshot["stage"], "finalization")
            self.assertNotEqual(snapshot["reported_state"], "completed")

    def test_startup_rebuilds_missing_terminal_progress_from_both_durable_stores(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller = ExecutionController(root)
            controller.register_handler(
                "tests.progress_success", progress_success_handler, execution_class="read_only"
            )
            task = controller.submit("tests.progress_success", {"request_id": "safe-fixture"})
            controller.run_once(task.id)

            journal_path = controller.db_path.parent / "progress.jsonl"
            journal_path.write_text("", encoding="utf-8")
            restarted = ExecutionController(root)

            snapshot = restarted.progress_journal.snapshot(task.id)
            self.assertEqual(snapshot["reported_state"], "completed")
            self.assertEqual(snapshot["stage"], "startup-reconciliation")
            self.assertEqual(snapshot["event_count"], 1)
            self.assertFalse(any("progress_reconciliation_required" in finding
                                 for finding in restarted.health_findings()))

    def test_startup_blocks_terminal_sqlite_row_without_terminal_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller = ExecutionController(root)
            controller.register_handler(
                "tests.progress_success", progress_success_handler, execution_class="read_only"
            )
            task = controller.submit("tests.progress_success", {"request_id": "safe-fixture"})

            def fail_finish(*args, **kwargs):
                raise OSError("controlled receipt store failure")

            controller.company.finish = fail_finish
            with self.assertRaisesRegex(OSError, "controlled receipt store failure"):
                controller.run_once(task.id)

            restarted = ExecutionController(root)
            snapshot = restarted.progress_journal.snapshot(task.id, stale_after_seconds=3600)
            self.assertEqual(restarted.task(task.id).state, "completed")
            self.assertEqual(snapshot["reported_state"], "blocked")
            self.assertEqual(snapshot["stage"], "startup-reconciliation")
            self.assertTrue(any("progress_reconciliation_required=1" in finding
                                for finding in restarted.health_findings()))

    def test_startup_correction_can_supersede_false_terminal_journal_event(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller = ExecutionController(root)
            controller.register_handler(
                "tests.progress_success", progress_success_handler, execution_class="read_only"
            )
            task = controller.submit("tests.progress_success", {"request_id": "safe-fixture"})
            controller.progress_journal.record(
                task.id, "corrupt_fixture_terminal", state="completed", stage="fixture",
                message="Injected stale terminal projection.",
            )

            restarted = ExecutionController(root)
            snapshot = restarted.progress_journal.snapshot(task.id, stale_after_seconds=3600)
            self.assertEqual(snapshot["reported_state"], "queued")
            self.assertEqual(snapshot["stage"], "startup-reconciliation")
            events = [row["event"] for row in restarted.progress_journal._events()
                      if row["task_id"] == task.id]
            self.assertEqual(events[-1], "startup_reconciliation")

    def test_startup_marks_expired_running_lease_as_recovering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller = ExecutionController(root)
            controller.register_handler(
                "tests.progress_success", progress_success_handler, execution_class="read_only"
            )
            task = controller.submit("tests.progress_success", {"request_id": "safe-fixture"})
            claimed = controller._claim(task.id)
            self.assertIsNotNone(claimed)
            with __import__("sqlite3").connect(controller.db_path) as db:
                stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
                db.execute(
                    "UPDATE tasks SET heartbeat_at=?,lease_until=? WHERE id=?",
                    (stale, stale, task.id),
                )

            restarted = ExecutionController(root)
            snapshot = restarted.progress_journal.snapshot(task.id, stale_after_seconds=3600)
            self.assertEqual(snapshot["reported_state"], "recovering")
            self.assertTrue(any("progress_reconciliation_required=1" in finding
                                for finding in restarted.health_findings()))

    def test_reconciliation_refuses_to_overwrite_a_newer_journal_event(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ProgressJournal(Path(temp) / "progress.jsonl")
            journal.record("task-1", "task_started", state="running", stage="dispatch")
            journal.record("task-1", "heartbeat", state="running", stage="handler")

            with self.assertRaisesRegex(RuntimeError, "changed during reconciliation"):
                journal.record_reconciliation(
                    "task-1", state="recovering", stage="startup-reconciliation",
                    message="Stale snapshot must not overwrite a concurrent heartbeat.",
                    expected_sequence=1,
                )

            self.assertEqual(journal.snapshot("task-1")["sequence"], 2)
            self.assertEqual(journal.snapshot("task-1")["reported_state"], "running")

    def test_bounded_startup_reconciliation_reports_and_clears_incomplete_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller = ExecutionController(root)
            controller.register_handler(
                "tests.progress_success", progress_success_handler, execution_class="read_only"
            )
            controller.submit("tests.progress_success", {"request_id": "first"})
            controller.submit("tests.progress_success", {"request_id": "second"})

            partial = controller.reconcile_progress_journal(limit=1, time_budget_seconds=2.0)
            self.assertTrue(partial["incomplete"])
            self.assertIsNotNone(partial["next_action"])
            self.assertTrue(any("progress_reconciliation_incomplete" in finding
                                for finding in controller.health_findings()))

            complete = controller.reconcile_progress_journal(limit=100, time_budget_seconds=5.0)
            self.assertFalse(complete["incomplete"])
            self.assertIsNone(complete["next_action"])
            self.assertFalse(any("progress_reconciliation_incomplete" in finding
                                 for finding in controller.health_findings()))

    def test_restart_blocks_when_execution_record_commit_fails_after_task_finalization(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller = ExecutionController(root)
            controller.register_handler(
                "tests.progress_success", progress_success_handler, execution_class="read_only"
            )
            task = controller.submit("tests.progress_success", {"request_id": "safe-fixture"})
            original_record = controller.company.record

            def fail_execution_record(*args, **kwargs):
                kind = args[1] if len(args) > 1 else kwargs.get("kind")
                if kind == "execution":
                    raise OSError("controlled execution-record commit failure")
                return original_record(*args, **kwargs)

            controller.company.record = fail_execution_record
            with self.assertRaisesRegex(OSError, "execution-record commit failure"):
                controller.run_once(task.id)

            self.assertEqual(controller.task(task.id).state, "completed")
            self.assertEqual(controller.company._state(task.work_unit_id)[0], "running")
            restarted = ExecutionController(root)
            snapshot = restarted.progress_journal.snapshot(task.id, stale_after_seconds=3600)
            self.assertEqual(snapshot["reported_state"], "blocked")
            self.assertTrue(any("progress_reconciliation_required=1" in finding
                                for finding in restarted.health_findings()))

    def test_restart_repairs_terminal_journal_write_failure_after_receipt_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller = ExecutionController(root)
            controller.register_handler(
                "tests.progress_success", progress_success_handler, execution_class="read_only"
            )
            task = controller.submit("tests.progress_success", {"request_id": "safe-fixture"})
            original_record = controller.progress_journal.record

            def fail_terminal_event(task_id, event, **kwargs):
                if event == "task_terminal":
                    raise OSError("controlled terminal journal write failure")
                return original_record(task_id, event, **kwargs)

            controller.progress_journal.record = fail_terminal_event
            result = controller.run_once(task.id)
            self.assertIsNotNone(result)
            self.assertEqual(controller.task(task.id).state, "completed")
            self.assertEqual(controller.company._state(task.work_unit_id)[0], "completed")
            before_restart = controller.progress_journal.snapshot(task.id, stale_after_seconds=3600)
            self.assertNotEqual(before_restart["reported_state"], "completed")

            restarted = ExecutionController(root)
            after_restart = restarted.progress_journal.snapshot(task.id, stale_after_seconds=3600)
            self.assertEqual(after_restart["reported_state"], "completed")
            self.assertEqual(after_restart["event"], "startup_reconciliation")
            self.assertFalse(any("progress_reconciliation_required" in finding
                                 for finding in restarted.health_findings()))

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
