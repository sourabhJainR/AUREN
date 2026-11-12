import subprocess
import sys
import json
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
            self.assertEqual(payload["tasks"][0]["lifecycle"]["domain"], "progress_task")
            self.assertEqual(payload["tasks"][0]["lifecycle"]["category"], "ready")
            self.assertEqual(payload["returned_task_lifecycle_counts"], {"ready": 1})
            human = self.invoke("progress", "--project-root", str(root))
            self.assertEqual(human.returncode, 0, human.stderr)
            self.assertIn("lifecycle: Queued (ready)", human.stdout)
            self.assertNotIn("Engineering HTML report", result.stdout)

    def test_progress_cli_reads_the_canonical_local_runtime_journal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            auren_home = Path(temp) / "auren-home"
            project_key = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:24]
            journal_path = (
                auren_home / "local-runtime" / project_key
                / "adaptive-runtime" / ".auren" / "progress.jsonl"
            )
            ProgressJournal(journal_path).record(
                "shared-task", "accepted", state="queued", stage="queued",
                message="Visible from the shared local runtime.",
            )
            with patch.dict(os.environ, {"AUREN_HOME": str(auren_home)}):
                result = self.invoke("progress", "--project-root", str(root), "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["tasks"][0]["task_id"], "shared-task")


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
            self.assertEqual(payload["tasks"][0]["lifecycle"]["category"], "stale")
            self.assertTrue(payload["tasks"][0]["lifecycle"]["attention_required"])
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
            self.assertEqual(payload["returned_task_lifecycle_counts"], {})


if __name__ == "__main__":
    unittest.main()
