from datetime import datetime, timezone
from pathlib import Path

import pytest

from portable.crew_adapters import TaskRetryBudget
from portable.resumable_provider import ProviderCallError, ResumableProviderExecutor


def test_connection_reset_fails_over_with_stable_idempotency_key_and_shared_budget():
    calls = []

    def broken(request, key):
        calls.append(("primary", key))
        raise ConnectionResetError("connection reset")

    def backup(request, key):
        calls.append(("backup", key))
        return {"proposal": "ok"}

    budget = TaskRetryBudget(max_retries=2)
    checkpoints, evidence = [], []
    executor = ResumableProviderExecutor(
        task_id="task-1", stage_id="crew-proposal",
        providers={"primary": broken, "backup": backup},
        provider_versions={"primary": "v1", "backup": "v2"},
        preferred_provider="primary", retry_budget=budget,
        persist=checkpoints.append, record_evidence=evidence.append,
    )
    result = executor.run({"prompt": "safe"})
    assert result == {"proposal": "ok"}
    assert calls[0][1] == calls[1][1]
    assert budget.used == 1
    assert checkpoints[-1]["status"] == "completed"
    assert evidence[-1]["checkpoint_digest"] == checkpoints[-1]["digest"]


def test_all_providers_rate_limited_checkpoint_and_resume_after_reset():
    reset = "2026-10-15T12:01:00+00:00"
    calls = []

    def limited(name):
        def invoke(request, key):
            calls.append(name)
            raise ProviderCallError("rate limited", status_code=429, reset_at=reset)
        return invoke

    checkpoint = []
    budget = TaskRetryBudget(max_retries=3)
    executor = ResumableProviderExecutor(
        task_id="task-2", stage_id="long-task",
        providers={"a": limited("a"), "b": limited("b")},
        provider_versions={"a": "1", "b": "2"}, preferred_provider="a",
        retry_budget=budget, persist=checkpoint.append,
    )
    deferred = executor.run({"prompt": "safe"}, now=datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc))
    assert deferred["status"] == "deferred"
    assert deferred["resume_at"] == reset
    saved = checkpoint[-1]
    assert budget.used == 1

    resumed_budget = TaskRetryBudget(max_retries=3)
    # Resume under a different preferred provider to support operator provider changes.
    calls_after_reset = []
    resumed = ResumableProviderExecutor(
        task_id="task-2", stage_id="long-task",
        providers={
            "a": lambda request, key: calls_after_reset.append("a") or {"result": "a"},
            "b": lambda request, key: calls_after_reset.append("b") or {"result": "b"},
        },
        provider_versions={"a": "1", "b": "2"}, preferred_provider="b",
        retry_budget=resumed_budget, persist=checkpoint.append, checkpoint=saved,
    )
    result = resumed.run({"prompt": "safe"}, now=datetime(2026, 10, 15, 12, 1, tzinfo=timezone.utc))
    assert result == {"result": "b"}
    assert calls_after_reset == ["b"]


def test_provider_checkpoint_tamper_and_wrong_task_fail_closed():
    executor = ResumableProviderExecutor(
        task_id="task-3", stage_id="stage",
        providers={"p": lambda request, key: {"ok": True}},
        provider_versions={"p": "1"}, preferred_provider="p",
        retry_budget=TaskRetryBudget(max_retries=1), persist=lambda _: None,
    )
    snapshot = executor.snapshot()
    tampered = dict(snapshot)
    tampered["stage_id"] = "other"
    with pytest.raises(ValueError, match="digest mismatch"):
        ResumableProviderExecutor(
            task_id="task-3", stage_id="stage",
            providers={"p": lambda request, key: {"ok": True}},
            provider_versions={"p": "1"}, preferred_provider="p",
            retry_budget=TaskRetryBudget(max_retries=1), persist=lambda _: None,
            checkpoint=tampered,
        )
    with pytest.raises(ValueError, match="different task or stage"):
        ResumableProviderExecutor(
            task_id="other", stage_id="stage",
            providers={"p": lambda request, key: {"ok": True}},
            provider_versions={"p": "1"}, preferred_provider="p",
            retry_budget=TaskRetryBudget(max_retries=1), persist=lambda _: None,
            checkpoint=snapshot,
        )
