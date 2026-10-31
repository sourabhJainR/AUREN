from __future__ import annotations

import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from portable.dashboard_server import _Handler
from portable.muxin_session_adapter import MuxinSessionAdapter
from portable.session_state import SessionCheckpoint, SessionStore


class _Snapshot:
    def as_dict(self):
        return {
            "generated_at": "2026-10-31T10:00:00+00:00",
            "project": "demo",
            "tasks": {"progress": [{
                "task_id": "task-active", "state": "running",
                "status_category": "active", "stage": "testing",
                "last_update": "2026-10-31T09:59:00+00:00",
                "seconds_since_update": 60, "progress_percent": 50,
                "next_action": "continue validation",
                "message": "private prompt must not be returned",
            }]},
            "plan_runs": [{
                "run_id": "plan-1", "status": "running", "revision": 3,
                "task_counts": {"running": 1}, "evidence_count": 2,
                "source_revision": "a" * 40,
                "goal": "private plan goal must not be returned",
            }],
        }


class _Dashboard:
    def snapshot(self):
        return _Snapshot()


def test_session_snapshot_combines_checkpoints_and_safe_journal_projection(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    project_key = store.project_key(tmp_path)
    for session_id, task_id, stage, error in (
        ("active-session", "task-active", "testing", None),
        ("completed-session", "task-done", "completed", None),
        ("blocked-session", "task-blocked", "blocked", "Bearer sensitive-error"),
    ):
        store.save(SessionCheckpoint(
            session_id=session_id, task_id=task_id, project_key=project_key,
            stage=stage, project_root=str(tmp_path), last_error=error,
            completed_batches=["unit"] if stage == "completed" else [],
            remaining_batches=["integration"] if stage == "testing" else [],
        ))

    adapter = MuxinSessionAdapter(tmp_path, session_store=store, dashboard=_Dashboard())
    payload = adapter.session_snapshot()
    assert payload["counts"] == {
        "sessions": 3, "active": 1, "completed": 1, "blocked": 1,
        "unverified": 0, "tasks": 1, "plan_runs": 1,
    }
    assert {row["session_id"] for row in payload["sessions"]} == {
        "active-session", "completed-session", "blocked-session",
    }
    assert next(row for row in payload["sessions"] if row["session_id"] == "blocked-session")["error_present"]
    serialized = json.dumps(payload)
    assert "sensitive-error" not in serialized
    assert "private prompt" not in serialized
    assert "private plan goal" not in serialized
    assert payload["tasks"][0]["task_id"] == "task-active"
    assert payload["plan_runs"][0]["run_id"] == "plan-1"


def test_dashboard_sessions_endpoint_serves_live_snapshot_and_rejects_foreign_origin():
    class _ControlCenter:
        def session_snapshot(self):
            return {"schema_version": 1, "sessions": [], "tasks": [], "plan_runs": []}

    handler = type("SessionDashboardHandler", (_Handler,), {"control_center": _ControlCenter()})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request("GET", "/api/sessions")
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        assert response.status == 200
        assert payload["schema_version"] == 1
        assert payload["sessions"] == []

        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request("GET", "/api/sessions", headers={"Origin": "https://attacker.example"})
        response = connection.getresponse()
        response.read()
        connection.close()
        assert response.status == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
