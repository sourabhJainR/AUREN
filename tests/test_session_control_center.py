"""Regression tests for the live session control-center adapter."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.muxin_session_adapter import LocalModelDiscovery, MuxinSessionAdapter, stage_progress
from portable.session_state import SessionCheckpoint, SessionStore


class SessionControlCenterTests(unittest.TestCase):
    def test_progress_is_stage_derived_and_unknown_is_not_success(self):
        self.assertEqual(stage_progress("queued"), 0)
        self.assertEqual(stage_progress("running"), 33)
        self.assertEqual(stage_progress("completed"), 100)
        self.assertEqual(stage_progress("made_up_stage"), 0)

    def test_session_projection_uses_checkpoint_and_is_project_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = SessionStore(root / "sessions")
            store.save(SessionCheckpoint(
                session_id="run-1", task_id="task-1",
                project_key=store.project_key(root), stage="testing",
                project_root=str(root), completed_batches=["unit-tests"],
                remaining_batches=["integration-tests"], attempt=2,
            ))
            adapter = MuxinSessionAdapter(root, session_store=store)
            rows = adapter.sessions()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["session_id"], "run-1")
            self.assertEqual(rows[0]["stage"], "testing")
            self.assertEqual(rows[0]["progress"], stage_progress("testing"))
            self.assertEqual(rows[0]["completed_batches"], ["unit-tests"])
            self.assertEqual(rows[0]["remaining_batches"], ["integration-tests"])

    def test_corrupt_checkpoint_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = SessionStore(root / "sessions")
            store.root.mkdir()
            (store.root / "broken.json").write_text("{", encoding="utf-8")
            self.assertEqual(MuxinSessionAdapter(root, session_store=store).sessions(), [])

    def test_local_model_discovery_rejects_non_loopback_endpoint(self):
        with self.assertRaises(ValueError):
            LocalModelDiscovery("https://example.com")

    def test_local_model_discovery_fails_fast_when_endpoint_is_absent(self):
        result = LocalModelDiscovery("http://127.0.0.1:1", timeout_seconds=0.1).discover()
        self.assertFalse(result["available"])
        self.assertEqual(result["models"], [])
        self.assertIn("latency_ms", result)


    def test_checkpoint_recovery_clears_error_and_increments_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = SessionStore(root / "sessions")
            checkpoint = SessionCheckpoint(
                session_id="recover-1", task_id="task-1",
                project_key=store.project_key(root), stage="running",
                project_root=str(root), attempt=1, last_error="temporary provider failure",
            )
            store.save(checkpoint)
            recovered = store.recover("recover-1", next_stage="planning")
            self.assertIsNotNone(recovered)
            self.assertEqual("planning", recovered.stage)
            self.assertEqual(2, recovered.attempt)
            self.assertIsNone(recovered.last_error)
            self.assertIsNotNone(store.load("recover-1"))



if __name__ == "__main__":
    unittest.main()
