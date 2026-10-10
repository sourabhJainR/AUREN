from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from portable.adaptive_runtime import AdaptiveRuntime
from portable.orchestration import Graph, Node, NodeKind


def test_default_runtime_uses_bounded_recovery_ledger_and_alternate_plan(tmp_path: Path):
    graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda state: "ok")])
    runtime = AdaptiveRuntime(graph, state_root=tmp_path / "state")
    observed_contexts = []

    def failing_execution(**kwargs):
        observed_contexts.append(dict(kwargs.get("context") or {}))
        raise RuntimeError("repeatable test failure")

    runtime._run_once = failing_execution
    with pytest.raises(RuntimeError, match="stopped safely"):
        runtime.run(
            session_id="recovery-session",
            task_id="recovery-task",
            project_root=tmp_path,
            intent="exercise durable recovery",
            context={"auren_max_iterations": 3},
        )

    with sqlite3.connect(runtime.execution_controller.db_path) as db:
        work_id = db.execute(
            "SELECT work_unit_id FROM sessions WHERE session_id=?",
            ("recovery-session:recovery-task",),
        ).fetchone()[0]
    attempts = runtime.recovery_ledger.attempts(work_id)
    assert [item.hypothesis for item in attempts] == [
        "default-plan", "default-plan", "alternate-plan-1",
    ]
    assert [item.action for item in attempts] == [
        "reproduce", "narrow_fix", "alternate_implementation",
    ]
    assert all(item.outcome == "failed" for item in attempts)
    assert [item["auren_recovery_mode"] for item in observed_contexts] == [
        "initial", "fresh-retry", "alternate-plan",
    ]
    checkpoint = runtime.recovery_ledger.get_checkpoint(work_id, "execution")
    assert checkpoint is not None
    assert checkpoint.status == "in_progress"
    assert checkpoint.payload["hypothesis"] == "alternate-plan-1"


def test_default_runtime_rejects_unbounded_iteration_requests(tmp_path: Path):
    graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda state: "ok")])
    runtime = AdaptiveRuntime(graph, state_root=tmp_path / "state")
    with pytest.raises(ValueError, match="between 1 and 12"):
        runtime.run(
            session_id="bounded-session",
            task_id="bounded-task",
            project_root=tmp_path,
            intent="bound iteration count",
            context={"auren_max_iterations": 1000000},
        )
