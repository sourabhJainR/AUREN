from pathlib import Path
import sqlite3
from datetime import datetime, timedelta, timezone

from portable.execution_controller import ExecutionController

def test_resume_pending_task_after_controller_restart(tmp_path: Path):
    first = ExecutionController(tmp_path)
    first.register_handler("tests.test_execution_controller.durable_handler", durable_handler)
    task = first.submit("tests.test_execution_controller.durable_handler", {"id": "restart"})
    second = ExecutionController(tmp_path)
    scores = second.resume_pending()
    assert scores
    assert second.task(task.id).state == "completed"

def test_resume_waits_for_live_lease_and_reclaims_expired_running_task(tmp_path: Path):
    first = ExecutionController(tmp_path)
    first.register_handler("tests.test_execution_controller.durable_handler", durable_handler)
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


def durable_handler(payload):
    return {"success": True, "evidence": [payload["id"]]}
