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
                return ProgressJournal(path, lock_timeout_seconds=10.0).record("shared", "unit", message=str(index)).sequence
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
                with self.assertRaisesRegex(TimeoutError, "lock acquisition timed out"):
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

    def test_corrupt_json_line_blocks_snapshot_and_future_writes_without_rewriting_history(self):
        from portable.progress_journal import ProgressJournalCorruption

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "progress.jsonl"
            journal = ProgressJournal(path)
            journal.record("task", "started", stage="plan")
            with path.open("a", encoding="utf-8") as stream:
                stream.write('{"task_id":"task","sequence":2,broken-json}\n')
            original = path.read_bytes()

            with self.assertRaisesRegex(ProgressJournalCorruption, "line 2"):
                journal.snapshot("task")
            with self.assertRaisesRegex(ProgressJournalCorruption, "line 2"):
                journal.record("task", "must-not-append")

            self.assertEqual(path.read_bytes(), original)

    def test_invalid_record_shape_and_invalid_utf8_fail_closed(self):
        from portable.progress_journal import ProgressJournalCorruption

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "progress.jsonl"
            path.write_text('{"task_id":"task"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ProgressJournalCorruption, "invalid record"):
                ProgressJournal(path).snapshot("task")

            path.write_bytes(b"\xff\xfe")
            with self.assertRaisesRegex(ProgressJournalCorruption, "UTF-8"):
                ProgressJournal(path).snapshot("task")

    def test_response_chunk_acknowledgements_resume_after_restart(self):
        from portable.response_delivery import chunk_response, chunks_after

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "progress.jsonl"
            journal = ProgressJournal(path)
            manifest, chunks = chunk_response("A long response that must be delivered losslessly. " * 20, max_bytes=80)
            journal.record("delivery", "started", stage="response")
            journal.record_response_chunk_ack(
                "delivery", response_id=manifest.response_id, response_sha256=manifest.sha256,
                acknowledged_index=1, total_chunks=manifest.total_chunks,
            )
            restarted = ProgressJournal(path)
            checkpoint = restarted.response_delivery_checkpoint("delivery", manifest.response_id)
            self.assertEqual(checkpoint["acknowledged_index"], 1)
            self.assertEqual(chunks_after(chunks, checkpoint["acknowledged_index"])[0].index, 2)
            restarted.record_response_chunk_ack(
                "delivery", response_id=manifest.response_id, response_sha256=manifest.sha256,
                acknowledged_index=2, total_chunks=manifest.total_chunks,
            )
            self.assertEqual(
                ProgressJournal(path).response_delivery_checkpoint("delivery", manifest.response_id)["acknowledged_index"],
                2,
            )

    def test_response_chunk_ack_is_idempotent_and_rejects_gaps_or_manifest_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ProgressJournal(Path(temp) / "progress.jsonl")
            digest = "a" * 64
            first = journal.record_response_chunk_ack(
                "delivery", response_id=digest, response_sha256=digest,
                acknowledged_index=1, total_chunks=3,
            )
            duplicate = journal.record_response_chunk_ack(
                "delivery", response_id=digest, response_sha256=digest,
                acknowledged_index=1, total_chunks=3,
            )
            self.assertEqual(first.sequence, duplicate.sequence)
            with self.assertRaisesRegex(ValueError, "contiguous"):
                journal.record_response_chunk_ack(
                    "delivery", response_id=digest, response_sha256=digest,
                    acknowledged_index=3, total_chunks=3,
                )
            with self.assertRaisesRegex(ValueError, "manifest changed"):
                journal.record_response_chunk_ack(
                    "delivery", response_id=digest, response_sha256=digest,
                    acknowledged_index=2, total_chunks=4,
                )
            with self.assertRaisesRegex(ValueError, "match"):
                journal.record_response_chunk_ack(
                    "delivery", response_id=digest, response_sha256="b" * 64,
                    acknowledged_index=2, total_chunks=3,
                )

    def test_response_delivery_checkpoint_rejects_corrupt_ack_history(self):
        from portable.progress_journal import ProgressJournalCorruption

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "progress.jsonl"
            journal = ProgressJournal(path)
            digest = "c" * 64
            journal.record_response_chunk_ack(
                "delivery", response_id=digest, response_sha256=digest,
                acknowledged_index=1, total_chunks=3,
            )
            with path.open("a", encoding="utf-8") as stream:
                import json
                row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
                row["sequence"] = 2
                row["completed_units"] = 3
                stream.write(json.dumps(row) + "\n")
            with self.assertRaisesRegex(ProgressJournalCorruption, "not contiguous"):
                ProgressJournal(path).response_delivery_checkpoint("delivery", digest)

    def test_invalid_progress_units_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                ProgressJournal(Path(temp) / "p.jsonl").record("f", "bad", completed_units=4, total_units=3)

    def test_priority_stack_keeps_interruptions_durable_and_orders_by_priority_fifo(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "progress.jsonl"
            journal = ProgressJournal(path)
            journal.record("active", "started", state="running", timestamp="2026-01-01T00:00:00+00:00")
            journal.record_interrupt("low", priority=20, message="low priority", parent_task_id="active",
                                     timestamp="2026-01-01T00:00:01+00:00")
            journal.record_interrupt("high", priority=90, message="urgent", parent_task_id="active",
                                     timestamp="2026-01-01T00:00:02+00:00")
            restarted = ProgressJournal(path)
            stack = restarted.priority_stack()
            self.assertEqual([item["task_id"] for item in stack], ["high", "low", "active"])
            self.assertEqual(restarted.next_ready()["task_id"], "high")
            self.assertEqual(restarted.snapshot("low")["parent_task_id"], "active")

    def test_tradeoff_waits_for_human_without_blocking_unrelated_work(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ProgressJournal(Path(temp) / "progress.jsonl")
            journal.record_interrupt("needs-confirmation", priority=100, message="material trade-off",
                                     decision_required=True)
            journal.record_interrupt("independent", priority=50, message="safe independent action")
            self.assertEqual(journal.snapshot("needs-confirmation")["state"], "blocked")
            self.assertTrue(journal.snapshot("needs-confirmation")["decision_required"])
            self.assertEqual(journal.next_ready()["task_id"], "independent")
            self.assertEqual(len(journal.priority_stack()), 2)

    def test_priority_validation_rejects_invalid_values(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ProgressJournal(Path(temp) / "progress.jsonl")
            for value in (-1, 101, True, 1.5):
                with self.assertRaises(ValueError):
                    journal.record_interrupt("bad", priority=value, message="invalid")

    def test_priority_and_confirmation_metadata_survive_later_events(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ProgressJournal(Path(temp) / "progress.jsonl")
            journal.record_interrupt("task", priority=80, message="review trade-off",
                                     decision_required=True)
            journal.record("task", "waiting", state="blocked", stage="approval")
            restarted = ProgressJournal(journal.path)
            snapshot = restarted.snapshot("task")
            self.assertEqual(snapshot["priority"], 80)
            self.assertTrue(snapshot["decision_required"])
            self.assertEqual(snapshot["stage"], "approval")

if __name__ == "__main__":
    unittest.main()
