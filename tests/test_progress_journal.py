import tempfile
import unittest
from datetime import datetime, timezone
import time
from threading import Event, Thread
from pathlib import Path
from portable.progress_journal import ProgressJournal, _journal_file_lock

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

    def test_file_lock_contention_fails_within_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "progress.jsonl"
            journal = ProgressJournal(path, lock_timeout_seconds=0.05)
            with _journal_file_lock(path, timeout_seconds=1.0):
                started = time.monotonic()
                with self.assertRaisesRegex(TimeoutError, "lock acquisition timed out"):
                    journal.record("blocked", "must-not-hang")
                self.assertLess(time.monotonic() - started, 0.5)

    def test_same_instance_lock_contention_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ProgressJournal(Path(temp) / "progress.jsonl", lock_timeout_seconds=0.05)
            acquired = Event()
            release = Event()

            def hold_instance_lock():
                with journal._lock:
                    acquired.set()
                    release.wait(1)

            worker = Thread(target=hold_instance_lock, daemon=True)
            worker.start()
            self.assertTrue(acquired.wait(0.5))
            started = time.monotonic()
            try:
                with self.assertRaisesRegex(TimeoutError, "thread lock acquisition timed out"):
                    journal.record("blocked-thread", "must-not-hang")
                self.assertLess(time.monotonic() - started, 0.5)
            finally:
                release.set()
                worker.join(timeout=0.5)

    def test_invalid_lock_timeout_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            for value in (0, -1, float("inf"), float("nan"), True):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    ProgressJournal(Path(temp) / "progress.jsonl", lock_timeout_seconds=value)

    def test_invalid_progress_units_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                ProgressJournal(Path(temp) / "p.jsonl").record("f", "bad", completed_units=4, total_units=3)

if __name__ == "__main__":
    unittest.main()
