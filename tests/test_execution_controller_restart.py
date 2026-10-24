from pathlib import Path
import sqlite3
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import time
import sys

from portable.execution_controller import ExecutionController, LeaseLostError

def test_process_death_running_task_recovers_in_fresh_process(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    seed_script = r"""
import sys
from pathlib import Path
from portable.execution_controller import ExecutionController
from tests.test_execution_controller import durable_handler
root = Path(sys.argv[1])
controller = ExecutionController(root)
controller.register_handler("tests.test_execution_controller.durable_handler", durable_handler, execution_class="read_only")
task = controller.submit("tests.test_execution_controller.durable_handler", {"id": "process-death"})
claimed = controller._claim(task.id)
assert claimed is not None and claimed.state == "running"
print(task.id)
"""
    seeded = subprocess.run(
        [sys.executable, "-c", seed_script, str(tmp_path)],
        cwd=repo_root, text=True, capture_output=True, check=True, timeout=10,
    )
    task_id = seeded.stdout.strip()
    assert task_id

    recovery_script = r"""
import json, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from portable.execution_controller import ExecutionController
root = Path(sys.argv[1])
task_id = sys.argv[2]
controller = ExecutionController(root)
live_lease_results = controller.resume_pending()
assert live_lease_results == [], "a fresh process must not steal a live worker lease"
expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
with sqlite3.connect(controller.db_path) as db:
    db.execute("UPDATE tasks SET lease_until=?, heartbeat_at=? WHERE id=?", (expired, expired, task_id))
recovered = controller.resume_pending()
task = controller.task(task_id)
print(json.dumps({
    "live_lease_results": len(live_lease_results),
    "recovered_results": len(recovered),
    "state": task.state,
    "attempts": task.attempts,
}))
"""
    recovered = subprocess.run(
        [sys.executable, "-c", recovery_script, str(tmp_path), task_id],
        cwd=repo_root, text=True, capture_output=True, check=True, timeout=10,
    )
    result = json.loads(recovered.stdout.strip().splitlines()[-1])
    assert result == {
        "live_lease_results": 0,
        "recovered_results": 1,
        "state": "completed",
        "attempts": 2,
    }

def slow_durable_handler(payload):
    time.sleep(float(payload.get("sleep_seconds", 0.08)))
    return {"success": True, "evidence": ["slow-handler"]}


def test_supervisor_renews_lease_while_handler_runs(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    controller._LEASE_HEARTBEAT_INTERVAL_SECONDS = 0.01
    controller.register_handler(
        "tests.test_execution_controller_restart.slow_durable_handler",
        slow_durable_handler,
        execution_class="read_only",
    )
    task = controller.submit(
        "tests.test_execution_controller_restart.slow_durable_handler",
        {"sleep_seconds": 0.08},
    )
    heartbeat_calls = []
    original_heartbeat = controller.heartbeat

    def count_heartbeat(claim, *, lease_seconds=300):
        heartbeat_calls.append(claim.lease_generation)
        return original_heartbeat(claim, lease_seconds=lease_seconds)

    controller.heartbeat = count_heartbeat
    score = controller.run_once(task.id)
    assert score is not None
    assert len(heartbeat_calls) >= 3
    assert controller.task(task.id).state == "completed"


def test_legacy_task_schema_adds_fencing_columns_without_losing_rows(tmp_path: Path):
    state_dir = tmp_path / ".auren"
    state_dir.mkdir(parents=True)
    db_path = state_dir / "execution.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE tasks("
            "id TEXT PRIMARY KEY, handler TEXT NOT NULL, payload TEXT NOT NULL, "
            "state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, work_unit_id TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, heartbeat_at TEXT, lease_until TEXT)"
        )
        db.execute(
            "INSERT INTO tasks(id,handler,payload,state,attempts,work_unit_id,created_at,updated_at,heartbeat_at,lease_until) "
            "VALUES('legacy-task','tests.test_execution_controller.durable_handler','{}','pending',0,'work','now','now','now','now')"
        )
    controller = ExecutionController(tmp_path)
    with sqlite3.connect(controller.db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
        row = db.execute("SELECT id,state,lease_generation,lease_token FROM tasks WHERE id='legacy-task'").fetchone()
    assert {"lease_generation", "lease_token"} <= columns
    assert row == ("legacy-task", "pending", 0, None)


def test_reclaimed_task_fences_old_worker_heartbeat_and_finalization(tmp_path: Path):
    first = ExecutionController(tmp_path)
    first.register_handler("tests.test_execution_controller.durable_handler", durable_handler, execution_class="read_only")
    task = first.submit("tests.test_execution_controller.durable_handler", {"id": "fenced-owner"})
    old_claim = first._claim(task.id)
    assert old_claim is not None

    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with sqlite3.connect(first.db_path) as db:
        db.execute("UPDATE tasks SET lease_until=? WHERE id=?", (expired, task.id))

    second = ExecutionController(tmp_path)
    new_claim = second._claim(task.id)
    assert new_claim is not None
    assert new_claim.lease_generation > old_claim.lease_generation
    assert new_claim.lease_token != old_claim.lease_token

    try:
        first.heartbeat(old_claim)
    except LeaseLostError as exc:
        assert "lease was lost" in str(exc)
    else:
        raise AssertionError("stale worker heartbeat must be rejected")
    assert not first._finalize_claim(old_claim, "completed")
    second.heartbeat(new_claim)
    assert second._finalize_claim(new_claim, "completed")
    assert second.task(task.id).state == "completed"


def test_resume_pending_task_after_controller_restart(tmp_path: Path):
    first = ExecutionController(tmp_path)
    first.register_handler("tests.test_execution_controller.durable_handler", durable_handler, execution_class="read_only")
    task = first.submit("tests.test_execution_controller.durable_handler", {"id": "restart"})
    second = ExecutionController(tmp_path)
    scores = second.resume_pending()
    assert scores
    assert second.task(task.id).state == "completed"

def test_resume_waits_for_live_lease_and_reclaims_expired_running_task(tmp_path: Path):
    first = ExecutionController(tmp_path)
    first.register_handler("tests.test_execution_controller.durable_handler", durable_handler, execution_class="read_only")
    task = first.submit("tests.test_execution_controller.durable_handler", {"id": "lease-restart"})
    claimed = first._claim(task.id)
    assert claimed is not None and claimed.state == "running"

    second = ExecutionController(tmp_path)
    assert second.resume_pending() == []
    assert second.task(task.id).state == "running"

    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with sqlite3.connect(second.db_path) as db:
        db.execute("UPDATE tasks SET lease_until=?, heartbeat_at=? WHERE id=?", (expired, expired, task.id))

    scores = second.resume_pending()
    assert scores
    assert second.task(task.id).state == "completed"
    assert second.task(task.id).attempts == 2


def test_recover_stale_respects_live_lease_and_only_resets_stale_task(tmp_path: Path):
    first = ExecutionController(tmp_path)
    first.register_handler("tests.test_execution_controller.durable_handler", durable_handler, execution_class="read_only")
    task = first.submit("tests.test_execution_controller.durable_handler", {"id": "stale-recovery"})
    assert first._claim(task.id) is not None

    second = ExecutionController(tmp_path)
    second.recover_stale(task.id, reason="race with live worker")
    assert second.task(task.id).state == "running"

    stale = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    with sqlite3.connect(second.db_path) as db:
        db.execute(
            "UPDATE tasks SET heartbeat_at=?, lease_until=? WHERE id=?",
            (stale, stale, task.id),
        )
    second.recover_stale(task.id, reason="confirmed stale worker")
    assert second.task(task.id).state == "pending"


def durable_handler(payload):
    return {"success": True, "evidence": [payload["id"]]}


def cancellation_aware_handler(payload):
    started = payload.get("started_event")
    if started is not None:
        started.set()
    cancel = payload["auren_cancel_event"]
    cancel.wait(timeout=3)
    return {"success": True, "detail": "handler observed cancellation", "evidence": ["cancel-aware"]}


def test_pending_task_cancellation_is_durable_and_never_dispatches(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    calls = []
    def handler(payload):
        calls.append(True)
        return {"success": True}
    controller.register_handler(
        "tests.test_execution_controller_restart.pending_cancel_handler",
        handler, execution_class="read_only",
    )
    task = controller.submit("tests.test_execution_controller_restart.pending_cancel_handler", {})
    assert controller.request_cancellation(task.id, reason="operator stop")
    assert controller.request_cancellation(task.id, reason="duplicate request")
    assert controller.run_once(task.id) is None
    assert controller.task(task.id).state == "cancelled"
    assert calls == []


def test_active_task_cancellation_reaches_handler_and_rejects_success(tmp_path: Path):
    controller = ExecutionController(tmp_path)
    started = Event()
    controller.register_handler(
        "tests.test_execution_controller_restart.cancellation_aware_handler",
        cancellation_aware_handler, execution_class="read_only",
    )
    task = controller.submit(
        "tests.test_execution_controller_restart.cancellation_aware_handler",
        {"started_event": started},
    )
    outcomes = []
    worker = Thread(target=lambda: outcomes.append(controller.run_once(task.id)), daemon=True)
    worker.start()
    assert started.wait(timeout=3), "handler did not start"
    assert controller.request_cancellation(task.id, reason="test cancellation")
    worker.join(timeout=5)
    assert not worker.is_alive(), "cancellation-aware handler did not exit"
    assert outcomes == [None]
    assert controller.task(task.id).state == "cancelled"
    with sqlite3.connect(controller.db_path) as db:
        requested = db.execute("SELECT cancel_requested FROM tasks WHERE id=?", (task.id,)).fetchone()
    assert requested == (1,)
