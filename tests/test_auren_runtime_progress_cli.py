import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from auren_cli import main as cli_main
from portable.progress_journal import ProgressJournal


class ProgressCliTests(unittest.TestCase):
    def test_json_command_reads_the_same_durable_journal_without_report_noise(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            journal = ProgressJournal(root / ".auren" / "progress.jsonl")
            journal.record(
                "task-1", "accepted", state="queued", stage="queued",
                message="Task durably queued.",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = cli_main([
                    "progress", "--project-root", str(root), "--json",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["tasks"][0]["task_id"], "task-1")
            self.assertEqual(payload["tasks"][0]["state"], "queued")
            self.assertNotIn("Engineering HTML report", output.getvalue())

    def test_stale_progress_is_explicit_and_includes_next_action(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            journal = ProgressJournal(root / ".auren" / "progress.jsonl")
            journal.record(
                "task-stale", "accepted", state="queued", stage="queued",
                message="Task durably queued.",
                timestamp="2020-01-01T00:00:00+00:00",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = cli_main([
                    "progress", "--project-root", str(root), "--task-id", "task-stale", "--json",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["tasks"][0]["state"], "stale")
            self.assertTrue(payload["tasks"][0]["next_action"])

    def test_missing_task_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = cli_main([
                    "progress", "--project-root", temp, "--task-id", "missing", "--json",
                ])
            self.assertEqual(code, 1)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "not_found")
            self.assertEqual(payload["tasks"], [])


if __name__ == "__main__":
    unittest.main()
