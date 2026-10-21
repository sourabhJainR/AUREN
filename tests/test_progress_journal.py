import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from portable.progress_journal import ProgressJournal

class ProgressJournalTests(unittest.TestCase):
    def test_durable_monotonic_events_and_truthful_percentage(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "progress.jsonl"
            journal = ProgressJournal(path)
            journal.record("a", "started", stage="plan")
            journal.record("a", "unit", stage="tests", completed_units=3, total_units=5)
            snapshot = ProgressJournal(path).snapshot("a")
            self.assertEqual(snapshot["sequence"], 2)
            self.assertEqual(snapshot["progress_percent"], 60.0)
            self.assertEqual(snapshot["event_count"], 2)

    def test_stale_work_is_visible_with_next_action(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ProgressJournal(Path(temp) / "progress.jsonl")
            journal.record("b", "started", timestamp="2026-01-01T00:00:00+00:00")
            snapshot = journal.snapshot("b", now=datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc), stale_after_seconds=30)
            self.assertEqual(snapshot["state"], "stale")
            self.assertTrue(snapshot["next_action"])

    def test_blocked_and_recovering_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ProgressJournal(Path(temp) / "progress.jsonl")
            journal.record("c", "provider-down", state="blocked")
            self.assertEqual(journal.snapshot("c")["state"], "blocked")
            journal.record("c", "recovery", state="recovering")
            self.assertEqual(journal.snapshot("c")["state"], "recovering")

    def test_terminal_state_rejects_silent_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ProgressJournal(Path(temp) / "progress.jsonl")
            journal.record("d", "done", state="completed")
            with self.assertRaises(ValueError):
                journal.record("d", "extra")

    def test_credential_like_message_is_redacted(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "progress.jsonl"
            ProgressJournal(path).record("e", "failure", message="api_key=abc123")
            self.assertNotIn("abc123", path.read_text(encoding="utf-8"))

    def test_multiple_journal_instances_keep_sequence_monotonic(self):
        import concurrent.futures
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "progress.jsonl"
            def write(index):
                return ProgressJournal(path).record("shared", "unit", message=str(index)).sequence
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                sequences = list(pool.map(write, range(80)))
            self.assertEqual(sorted(sequences), list(range(1, 81)))
            snapshot = ProgressJournal(path).snapshot("shared")
            self.assertEqual(snapshot["sequence"], 80)
            self.assertEqual(snapshot["event_count"], 80)

    def test_invalid_progress_units_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                ProgressJournal(Path(temp) / "p.jsonl").record("f", "bad", completed_units=4, total_units=3)

if __name__ == "__main__":
    unittest.main()
