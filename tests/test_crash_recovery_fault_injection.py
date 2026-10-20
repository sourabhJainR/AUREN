from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from portable.crash_recovery import CheckpointConflict, CheckpointStore


def _commit_checkpoint_then_crash(database: str, marker: str) -> None:
    with CheckpointStore(database) as store:
        checkpoint = store.save("episode-1", 1, "validated", {"revision": "abc", "count": 1})
    Path(marker).write_text(checkpoint.digest, encoding="utf-8")
    os._exit(71)


def _crash_with_uncommitted_checkpoint(database: str) -> None:
    connection = sqlite3.connect(database, timeout=5)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "INSERT INTO checkpoints(episode,seq,state,payload,digest) VALUES(?,?,?,?,?)",
        ("episode-uncommitted", 1, "partial", '{"revision":"half"}', "not-a-real-digest"),
    )
    os._exit(72)


def _run_crashing_process(target, *args) -> int:
    process = multiprocessing.get_context("spawn").Process(target=target, args=args)
    process.start()
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(3)
        raise AssertionError("fault-injection child did not terminate within its budget")
    return int(process.exitcode)


class CrashRecoveryFaultInjectionTests(unittest.TestCase):
    def test_committed_checkpoint_survives_abrupt_process_exit_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = str(Path(tmp) / "checkpoint.sqlite3")
            marker = str(Path(tmp) / "committed.digest")
            with CheckpointStore(database):
                pass
            self.assertEqual(71, _run_crashing_process(_commit_checkpoint_then_crash, database, marker))
            expected_digest = Path(marker).read_text(encoding="utf-8")
            with CheckpointStore(database) as restarted:
                checkpoint = restarted.latest("episode-1")
            self.assertIsNotNone(checkpoint)
            self.assertEqual("validated", checkpoint.state)
            self.assertEqual({"revision": "abc", "count": 1}, checkpoint.payload)
            self.assertEqual(expected_digest, checkpoint.digest)

    def test_uncommitted_checkpoint_is_not_visible_after_process_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = str(Path(tmp) / "checkpoint.sqlite3")
            with CheckpointStore(database):
                pass
            self.assertEqual(72, _run_crashing_process(_crash_with_uncommitted_checkpoint, database))
            with CheckpointStore(database) as restarted:
                self.assertIsNone(restarted.latest("episode-uncommitted"))

    def test_identical_checkpoint_retry_is_idempotent_across_payload_key_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            with CheckpointStore(Path(tmp) / "checkpoint.sqlite3") as store:
                first = store.save("episode-1", 3, "planned", {"a": 1, "b": 2})
                replay = store.save("episode-1", 3, "planned", {"b": 2, "a": 1})
                self.assertEqual(first, replay)
                self.assertEqual(first, store.latest("episode-1"))

    def test_same_checkpoint_identity_with_different_payload_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with CheckpointStore(Path(tmp) / "checkpoint.sqlite3") as store:
                store.save("episode-1", 4, "planned", {"revision": "abc"})
                with self.assertRaisesRegex(CheckpointConflict, "different immutable content"):
                    store.save("episode-1", 4, "planned", {"revision": "def"})
                self.assertEqual({"revision": "abc"}, store.latest("episode-1").payload)

    def test_same_checkpoint_identity_with_different_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with CheckpointStore(Path(tmp) / "checkpoint.sqlite3") as store:
                store.save("episode-1", 4, "planned", {"revision": "abc"})
                with self.assertRaises(CheckpointConflict):
                    store.save("episode-1", 4, "committed", {"revision": "abc"})

    def test_corrupt_stored_checkpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "checkpoint.sqlite3"
            with CheckpointStore(database) as store:
                store.save("episode-1", 1, "planned", {"revision": "abc"})
                store.cx.execute(
                    "UPDATE checkpoints SET payload=? WHERE episode=? AND seq=?",
                    ('{"revision":"tampered"}', "episode-1", 1),
                )
                store.cx.commit()
                with self.assertRaisesRegex(ValueError, "integrity failure"):
                    store.latest("episode-1")

    def test_invalid_sequences_and_non_finite_payloads_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with CheckpointStore(Path(tmp) / "checkpoint.sqlite3") as store:
                for sequence in (-1, True, 1.5):
                    with self.subTest(sequence=sequence), self.assertRaises(ValueError):
                        store.save("episode-1", sequence, "planned", {"revision": "abc"})
                with self.assertRaisesRegex(ValueError, "finite JSON"):
                    store.save("episode-1", 1, "planned", {"score": float("nan")})


if __name__ == "__main__":
    unittest.main()
