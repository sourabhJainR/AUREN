"""Regression coverage for quota-aware durable task recovery."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from portable.execution_controller import ExecutionController, ProviderQuotaError

_quota_calls = 0


def quota_wait_handler(payload):
    """Test handler that fails once with a typed quota signal, then succeeds."""
    global _quota_calls
    _quota_calls += 1
    if _quota_calls == 1:
        raise ProviderQuotaError(
            "provider rate limit",
            provider="test-provider",
            retry_after=payload.get("retry_after", 60),
            permanent=bool(payload.get("permanent", False)),
        )
    return {"success": True, "detail": "resumed from durable task"}


def _state(controller: ExecutionController, task_id: str):
    with sqlite3.connect(controller.db_path) as db:
        return db.execute(
            "SELECT state, next_retry_at, quota_provider, quota_blocked FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()


def test_quota_wait_is_durable_and_resumes_only_when_due(tmp_path):
    global _quota_calls
    _quota_calls = 0
    controller = ExecutionController(tmp_path)
    controller.register_handler("quota_wait_handler", quota_wait_handler, execution_class="read_only")
    task = controller.submit("quota_wait_handler", {"retry_after": 60})

    assert controller.run_once(task.id) is None
    state, retry_at, provider, blocked = _state(controller, task.id)
    assert state == "waiting_quota"
    assert retry_at is not None
    assert provider == "test-provider"
    assert blocked == 0
    assert controller.due_quota_task_ids(now=datetime.now(timezone.utc), limit=10) == []

    due = datetime.fromisoformat(retry_at) + timedelta(seconds=1)
    assert controller.wake_due_quota_tasks(now=due, limit=10) == [task.id]
    assert _state(controller, task.id)[0] == "pending"
    assert controller.run_once(task.id) is not None
    assert _state(controller, task.id)[0] == "completed"
    assert _quota_calls == 2


def test_permanent_quota_exhaustion_parks_without_polling(tmp_path):
    global _quota_calls
    _quota_calls = 0
    controller = ExecutionController(tmp_path)
    controller.register_handler("quota_wait_handler", quota_wait_handler, execution_class="read_only")
    task = controller.submit("quota_wait_handler", {"permanent": True})

    assert controller.run_once(task.id) is None
    state, retry_at, provider, blocked = _state(controller, task.id)
    assert state == "blocked_quota"
    assert retry_at is None
    assert provider == "test-provider"
    assert blocked == 1
    assert controller.due_quota_task_ids(limit=10) == []
    assert controller.run_once(task.id) is None
    assert _quota_calls == 1
