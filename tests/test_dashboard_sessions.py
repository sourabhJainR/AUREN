from __future__ import annotations

import http.client
import json
import sqlite3
from http.server import ThreadingHTTPServer
from pathlib import Path
import hashlib
import time
import tempfile
import unittest
from threading import Thread

from portable.dashboard_server import _Handler
from portable.muxin_session_adapter import MuxinSessionAdapter, workspace_registry_reader
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
    def live_session_projection(self):
        return _Snapshot().as_dict()


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

    stale = SessionCheckpoint(
        session_id="stale-session", task_id="task-stale", project_key=project_key,
        stage="running", project_root=str(tmp_path), updated_at=time.time() - 120,
    )
    stale_payload = stale.__dict__.copy()
    stale_payload["state_digest"] = ""
    stale.state_digest = hashlib.sha256(
        json.dumps(stale_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    store.root.mkdir(parents=True, exist_ok=True)
    store.path(stale.session_id).write_text(
        json.dumps(stale.__dict__), encoding="utf-8",
    )

    adapter = MuxinSessionAdapter(tmp_path, session_store=store, dashboard=_Dashboard())
    payload = adapter.session_snapshot()
    assert payload["counts"] == {
        "sessions": 4, "active": 1, "completed": 1, "blocked": 1, "stale": 1,
        "unverified": 0, "tasks": 1, "plan_runs": 1,
    }
    assert {row["session_id"] for row in payload["sessions"]} == {
        "active-session", "completed-session", "blocked-session", "stale-session",
    }
    assert next(row for row in payload["sessions"] if row["session_id"] == "blocked-session")["error_present"]
    assert next(row for row in payload["sessions"] if row["session_id"] == "stale-session")["status"] == "stale"
    serialized = json.dumps(payload)
    assert "sensitive-error" not in serialized
    assert "private prompt" not in serialized
    assert "private plan goal" not in serialized
    assert payload["tasks"][0]["task_id"] == "task-active"
    assert payload["plan_runs"][0]["run_id"] == "plan-1"


def test_dashboard_sessions_endpoint_serves_live_snapshot_and_rejects_foreign_origin():
    class _ControlCenter:
        def session_snapshot(self, *, offset=0, limit=50, query="", status="all"):
            return {
                "schema_version": 1,
                "pagination": {
                    "offset": offset, "limit": limit, "total": 75,
                    "has_more": offset + limit < 75, "query": query,
                    "status_filter": status,
                },
                "sessions": [{"session_id": f"session-{offset}"}],
                "tasks": [], "plan_runs": [],
            }

    handler = type("SessionDashboardHandler", (_Handler,), {"control_center": _ControlCenter()})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request("GET", "/api/sessions?offset=50&limit=25&q=engineer&status=complete")
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        assert response.status == 200
        assert payload["schema_version"] == 1
        assert payload["pagination"] == {"offset": 50, "limit": 25, "total": 75, "has_more": False, "query": "engineer", "status_filter": "complete"}
        assert payload["sessions"] == [{"session_id": "session-50"}]

        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request("GET", "/api/sessions?offset=-1&limit=25")
        response = connection.getresponse()
        response.read()
        connection.close()
        assert response.status == 400

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


def test_session_inventory_paginates_without_dropping_completed_records(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    project_key = store.project_key(tmp_path)
    for index in range(123):
        store.save(SessionCheckpoint(
            session_id=f"session-{index:03d}",
            task_id=f"task-{index:03d}",
            project_key=project_key,
            stage="completed" if index % 3 == 0 else "running",
            project_root=str(tmp_path),
        ))
    adapter = MuxinSessionAdapter(tmp_path, session_store=store, dashboard=_Dashboard())
    first = adapter.session_snapshot(offset=0, limit=50)
    second = adapter.session_snapshot(offset=50, limit=50)
    third = adapter.session_snapshot(offset=100, limit=50)
    all_ids = [
        row["session_id"]
        for page in (first, second, third)
        for row in page["sessions"]
    ]
    assert len(all_ids) == 123
    assert len(set(all_ids)) == 123
    assert first["pagination"] == {
        "offset": 0, "limit": 50, "total": 123, "has_more": True,
    }
    assert second["pagination"]["has_more"] is True
    assert third["pagination"]["has_more"] is False
    assert first["counts"]["sessions"] == 123
    assert first["counts"]["completed"] == 41
    assert first["counts"]["active"] == 82


def test_session_pagination_rejects_invalid_bounds(tmp_path):
    adapter = MuxinSessionAdapter(tmp_path, session_store=SessionStore(tmp_path / "sessions"), dashboard=_Dashboard())
    for offset, limit in ((-1, 10), (0, 0), (0, 101), (True, 10)):
        try:
            adapter.session_snapshot(offset=offset, limit=limit)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid pagination accepted: offset={offset}, limit={limit}")


def test_session_search_and_status_filter_cover_full_inventory(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    project_key = store.project_key(tmp_path)
    for index in range(123):
        store.save(SessionCheckpoint(
            session_id=f"session-{index:03d}",
            task_id=f"task-{index:03d}",
            project_key=project_key,
            stage="completed" if index % 3 == 0 else "running",
            active_provider="special-provider" if index == 121 else "default-provider",
            project_root=str(tmp_path),
            completed_batches=["release-validation"] if index == 121 else [],
        ))
    adapter = MuxinSessionAdapter(tmp_path, session_store=store, dashboard=_Dashboard())
    searched = adapter.session_snapshot(query="special-provider", limit=10)
    assert searched["pagination"]["total"] == 1
    assert searched["sessions"][0]["session_id"] == "session-121"
    assert searched["counts"]["sessions"] == 123
    completed = adapter.session_snapshot(status="complete", limit=10)
    assert completed["pagination"]["total"] == 41
    assert all(row["status"] == "complete" for row in completed["sessions"])
    batch = adapter.session_snapshot(query="release-validation")
    assert [row["session_id"] for row in batch["sessions"]] == ["session-121"]


class WorkspaceInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def adapter(self, reader=None):
        return MuxinSessionAdapter(
            self.root,
            session_store=SessionStore(self.root / "sessions"),
            dashboard=_Dashboard(),
            workspace_reader=reader,
        )

    def test_workspace_inventory_is_allowlisted_and_never_exposes_host_paths(self):
        adapter = self.adapter(lambda: [{
            "workspace_id": "workspace-1",
            "worker_id": "worker-1",
            "task_id": "task-1",
            "sandbox_id": "sandbox-1",
            "status": "proposal_ready",
            "base_revision": "a" * 40,
            "branch": "agent/worker-1",
            "updated_at": "2026-11-07T18:00:00+00:00",
            "owned_paths": ["src"],
            "changed_paths": ["src/app.py"],
            "evidence_refs": ["sha256:" + "b" * 64],
            "workspace_path": "/private/worker-workspaces/worker-1",
            "raw_output": "Bearer must-not-appear",
            "provider_token": "must-not-appear",
        }])
        inventory = adapter.session_snapshot()["workspaces"]
        self.assertEqual(inventory["status"], "available")
        self.assertEqual(inventory["total_count"], 1)
        self.assertFalse(inventory["truncated"])
        record = inventory["records"][0]
        self.assertEqual(record, {
            "workspace_id": "workspace-1",
            "worker_id": "worker-1",
            "task_id": "task-1",
            "sandbox_id": "sandbox-1",
            "status": "proposal_ready",
            "base_revision": "a" * 40,
            "branch": "agent/worker-1",
            "updated_at": "2026-11-07T18:00:00+00:00",
            "owned_paths": ["src"],
            "changed_paths": ["src/app.py"],
            "evidence_refs": ["sha256:" + "b" * 64],
            "lists_truncated": False,
        })
        serialized = json.dumps(inventory)
        self.assertNotIn("/private/worker-workspaces", serialized)
        self.assertNotIn("raw_output", serialized)
        self.assertNotIn("provider_token", serialized)
        self.assertNotIn("must-not-appear", serialized)

    def test_workspace_inventory_missing_or_failed_reader_is_not_reported_as_empty(self):
        unconfigured = self.adapter().session_snapshot()["workspaces"]
        self.assertEqual(unconfigured["status"], "not_configured")
        self.assertIsNone(unconfigured["total_count"])
        self.assertEqual(unconfigured["records"], [])

        def unavailable():
            raise TimeoutError("private host details must not escape")

        failed = self.adapter(unavailable).session_snapshot()["workspaces"]
        self.assertEqual(failed["status"], "unavailable")
        self.assertIsNone(failed["total_count"])
        self.assertEqual(failed["records"], [])
        self.assertEqual(failed["reason"], "TimeoutError")
        self.assertNotIn("private host details", json.dumps(failed))

    def test_workspace_inventory_is_bounded_and_rejects_unsafe_or_duplicate_identity(self):
        rows = [
            {"workspace_id": f"workspace-{index}", "status": "running",
             "changed_paths": [f"src/module_{index}.py"]}
            for index in range(26)
        ]
        inventory = self.adapter(lambda: rows).session_snapshot()["workspaces"]
        self.assertEqual(inventory["status"], "available")
        self.assertEqual(inventory["total_count"], 26)
        self.assertEqual(len(inventory["records"]), 25)
        self.assertTrue(inventory["truncated"])

        unsafe = self.adapter(lambda: [{
            "workspace_id": "workspace-unsafe", "status": "running",
            "changed_paths": ["../outside.py"],
        }]).session_snapshot()["workspaces"]
        self.assertEqual(unsafe["status"], "unavailable")
        self.assertEqual(unsafe["records"], [])
        self.assertEqual(unsafe["reason"], "ValueError")

        duplicate = self.adapter(lambda: [
            {"workspace_id": "same", "status": "running"},
            {"workspace_id": "same", "status": "stopped"},
        ]).session_snapshot()["workspaces"]
        self.assertEqual(duplicate["status"], "unavailable")
        self.assertEqual(duplicate["records"], [])
        self.assertEqual(duplicate["reason"], "ValueError")

    def test_workspace_registry_reader_projects_canonical_rows_without_host_paths(self):
        registry = self.root / "workspace-registry.sqlite3"
        connection = sqlite3.connect(registry)
        connection.execute(
            """CREATE TABLE graph_workspace_registry (
                worker_id TEXT PRIMARY KEY, task_id TEXT, base_revision TEXT NOT NULL,
                owned_paths TEXT NOT NULL, workspace_path TEXT NOT NULL,
                state TEXT NOT NULL, artifact_path TEXT, artifact_digest TEXT,
                proposal_paths TEXT, cancellation_reason TEXT, termination_evidence TEXT
            )"""
        )
        connection.execute(
            """INSERT INTO graph_workspace_registry
               (worker_id,task_id,base_revision,owned_paths,workspace_path,state,artifact_path,
                artifact_digest,proposal_paths)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            ("worker-7", "task-7", "a" * 40, json.dumps(["src"]), "/private/worktree/worker-7",
             "proposal_ready", "/private/artifacts/proposal.patch", "sha256:" + "c" * 64,
             json.dumps(["src/app.py"])),
        )
        connection.commit()
        connection.close()

        rows = workspace_registry_reader(registry)()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], {
            "workspace_id": "worker-7",
            "worker_id": "worker-7",
            "task_id": "task-7",
            "status": "proposal_ready",
            "base_revision": "a" * 40,
            "owned_paths": ["src"],
            "changed_paths": ["src/app.py"],
            "evidence_refs": ["sha256:" + "c" * 64],
        })
        self.assertNotIn("/private", json.dumps(rows))

    def test_workspace_registry_reader_requires_absolute_existing_non_symlink_path(self):
        with self.assertRaisesRegex(ValueError, "must be absolute"):
            workspace_registry_reader("relative/workspace-registry.sqlite3")

        missing = self.root / "missing.sqlite3"
        with self.assertRaises(FileNotFoundError):
            workspace_registry_reader(missing)()

        target = self.root / "real.sqlite3"
        connection = sqlite3.connect(target)
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.commit()
        connection.close()
        link = self.root / "registry-link.sqlite3"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable on this host")
        with self.assertRaisesRegex(PermissionError, "must not be a symlink"):
            workspace_registry_reader(link)()

    def test_workspace_panel_is_rendered_as_read_only_inventory(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "dashboard" / "index.html").read_text(encoding="utf-8")
        script = (root / "dashboard" / "assets" / "sessions.js").read_text(encoding="utf-8")
        self.assertIn('id="workspace-list"', html)
        self.assertIn('id="workspace-count"', html)
        self.assertIn("renderWorkspaces(data)", script)
        self.assertIn("No workspace state is inferred", script)
