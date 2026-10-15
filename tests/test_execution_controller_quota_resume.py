"""Regression coverage for quota-aware durable task recovery."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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


class ExecutionControllerQuotaResumeTests(unittest.TestCase):
    def setUp(self):
        global _quota_calls
        _quota_calls = 0
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.root = Path(self._tempdir.name)

    def test_quota_wait_is_durable_and_resumes_only_when_due(self):
        controller = ExecutionController(self.root)
        controller.register_handler("quota_wait_handler", quota_wait_handler, execution_class="read_only")
        task = controller.submit("quota_wait_handler", {"retry_after": 60})

        self.assertIsNone(controller.run_once(task.id))
        state, retry_at, provider, blocked = _state(controller, task.id)
        self.assertEqual(state, "waiting_quota")
        self.assertIsNotNone(retry_at)
        self.assertEqual(provider, "test-provider")
        self.assertEqual(blocked, 0)
        status = controller.quota_status(task.id)
        self.assertEqual(status["state"], "waiting_quota")
        self.assertFalse(status["operator_action_required"])
        self.assertIn("provider quota/rate-limit", status["reason"])
        self.assertEqual(controller.due_quota_task_ids(now=datetime.now(timezone.utc), limit=10), [])

        # A fresh controller instance must recover the same SQLite task and handler.
        controller = ExecutionController(self.root)
        retry_time = datetime.fromisoformat(retry_at)
        self.assertGreaterEqual(controller.seconds_until_quota_due(now=retry_time - timedelta(seconds=10)), 9.9)
        self.assertLessEqual(controller.seconds_until_quota_due(now=retry_time - timedelta(seconds=10)), 10.1)
        due = retry_time + timedelta(seconds=1)
        self.assertEqual(controller.wake_due_quota_tasks(now=due, limit=10), [task.id])
        self.assertEqual(_state(controller, task.id)[0], "pending")
        self.assertIsNotNone(controller.run_once(task.id))
        self.assertEqual(_state(controller, task.id)[0], "completed")
        self.assertEqual(_quota_calls, 2)

    def test_permanent_quota_exhaustion_parks_without_polling(self):
        controller = ExecutionController(self.root)
        controller.register_handler("quota_wait_handler", quota_wait_handler, execution_class="read_only")
        task = controller.submit("quota_wait_handler", {"permanent": True})

        self.assertIsNone(controller.run_once(task.id))
        state, retry_at, provider, blocked = _state(controller, task.id)
        self.assertEqual(state, "blocked_quota")
        self.assertIsNone(retry_at)
        self.assertEqual(provider, "test-provider")
        self.assertEqual(blocked, 1)
        self.assertTrue(controller.quota_status(task.id)["operator_action_required"])
        self.assertEqual(controller.due_quota_task_ids(limit=10), [])
        self.assertIsNone(controller.run_once(task.id))
        self.assertEqual(_quota_calls, 1)
        self.assertTrue(controller.unblock_quota_task(task.id, operator_reason="Provider account quota replenished"))
        self.assertEqual(_state(controller, task.id)[0], "pending")

    def test_http_429_uses_provider_reset_headers(self):
        class RateLimitResponse:
            status_code = 429
            headers = {"x-ratelimit-reset": str((datetime.now(timezone.utc) + timedelta(minutes=3)).timestamp())}

        class RateLimitError(Exception):
            response = RateLimitResponse()

        is_quota, provider, retry_after, permanent = ExecutionController._quota_signal(RateLimitError())
        self.assertTrue(is_quota)
        self.assertEqual(provider, "unknown")
        self.assertIsInstance(retry_after, datetime)
        self.assertFalse(permanent)
        self.assertGreater(retry_after, datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
