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

    def test_global_search_and_status_filters_apply_before_pagination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = SessionStore(root / "sessions")
            project_key = store.project_key(root)
            for index in range(123):
                store.save(SessionCheckpoint(
                    session_id=f"session-{index:03d}",
                    task_id=f"task-{index:03d}",
                    project_key=project_key,
                    stage="completed" if index % 3 == 0 else "running",
                    active_provider="provider-special" if index == 121 else "provider-default",
                    project_root=str(root),
                    completed_batches=["acceptance-suite"] if index == 121 else [],
                ))
            adapter = MuxinSessionAdapter(root, session_store=store, dashboard=_Dashboard())
            search = adapter.session_snapshot(query="provider-special", offset=0, limit=10)
            self.assertEqual(search["pagination"]["total"], 1)
            self.assertEqual(search["sessions"][0]["session_id"], "session-121")
            self.assertEqual(search["counts"]["sessions"], 123)
            complete = adapter.session_snapshot(status="complete", offset=0, limit=20)
            self.assertEqual(complete["pagination"]["total"], 41)
            self.assertTrue(all(row["status"] == "complete" for row in complete["sessions"]))
            batch = adapter.session_snapshot(query="acceptance-suite", offset=0, limit=10)
            self.assertEqual([row["session_id"] for row in batch["sessions"]], ["session-121"])

    def test_session_filter_validation_is_fail_closed(self):
        adapter = MuxinSessionAdapter(".", session_store=SessionStore("/tmp/auren-test-sessions"), dashboard=_Dashboard())
        for query, status in (("x" * 201, "all"), ("", "running")):
            try:
                adapter.session_snapshot(query=query, status=status)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid session filter was accepted")
