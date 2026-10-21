import subprocess
import sys
import json
import tempfile
import unittest
from pathlib import Path

from portable.progress_journal import ProgressJournal

ROOT = Path(__file__).resolve().parents[1]


class ProgressCliTests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "auren_cli.py"), *args],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def test_json_command_reads_the_same_durable_journal_without_report_noise(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            journal = ProgressJournal(root / ".auren" / "progress.jsonl")
            journal.record(
                "task-1", "accepted", state="queued", stage="queued",
                message="Task durably queued.",
            )
            result = self.invoke("progress", "--project-root", str(root), "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["tasks"][0]["task_id"], "task-1")
            self.assertEqual(payload["tasks"][0]["state"], "queued")
            self.assertNotIn("Engineering HTML report", result.stdout)

    def test_stale_progress_is_explicit_and_includes_next_action(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            journal = ProgressJournal(root / ".auren" / "progress.jsonl")
            journal.record(
                "task-stale", "accepted", state="queued", stage="queued",
                message="Task durably queued.",
                timestamp="2020-01-01T00:00:00+00:00",
            )
            result = self.invoke(
                "progress", "--project-root", str(root), "--task-id", "task-stale", "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["tasks"][0]["state"], "stale")
            self.assertTrue(payload["tasks"][0]["next_action"])

    def test_missing_task_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.invoke(
                "progress", "--project-root", temp, "--task-id", "missing", "--json",
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "not_found")
            self.assertEqual(payload["tasks"], [])


if __name__ == "__main__":
    unittest.main()
