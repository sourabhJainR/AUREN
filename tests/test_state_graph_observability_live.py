from __future__ import annotations

import json
from pathlib import Path

from portable.agency_observability import LocalExporter, Tracer
from portable.orchestration import Node, NodeKind, RunStatus
from portable.state_graph import StateGraph


def _trace_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_node_execution_spans_are_emitted_during_real_execution(tmp_path: Path) -> None:
    path = tmp_path / "live-traces.jsonl"
    graph = StateGraph.from_nodes([
        Node("research", NodeKind.AGENT, lambda state: {"answer": "ready"}),
        Node("verify", NodeKind.EVALUATOR, lambda state: {"verified": True}, depends_on=("research",)),
    ])
    tracer = Tracer(LocalExporter(path, enabled=True))

    result = graph.run("task-live", "run live nodes", tracer=tracer)

    assert result.status == RunStatus.ACCEPTED
    records = _trace_records(path)
    starts = [
        record for record in records
        if record.get("event_type") == "span_started"
        and (record.get("span") or {}).get("name", "").startswith("node_execution:")
    ]
    assert [record["span"]["name"] for record in starts] == [
        "node_execution:research", "node_execution:verify",
    ]
    supervisor_start = next(
        record for record in records
        if record.get("event_type") == "span_started"
        and (record.get("span") or {}).get("name") == "supervisor"
    )
    assert all(record["span"]["parent_id"] == supervisor_start["span"]["span_id"] for record in starts)
    finishes = [
        record for record in records
        if record.get("event_type") == "span_updated"
        and (record.get("span") or {}).get("name", "").startswith("node_execution:")
    ]
    assert len(finishes) == 2
    assert all(record["span"]["end"] is not None for record in finishes)
    assert all(record["span"]["status"] == "ok" for record in finishes)


def test_node_exception_has_live_error_span_not_only_post_run_summary(tmp_path: Path) -> None:
    path = tmp_path / "failed-live-traces.jsonl"

    def fail(_state):
        raise RuntimeError("synthetic node failure")

    graph = StateGraph.from_nodes([Node("provider_call", NodeKind.AGENT, fail)])
    tracer = Tracer(LocalExporter(path, enabled=True))

    result = graph.run("task-failed", "run failing node", tracer=tracer)

    assert result.status != RunStatus.ACCEPTED
    records = _trace_records(path)
    finished = [
        record for record in records
        if record.get("event_type") == "span_updated"
        and (record.get("span") or {}).get("name") == "node_execution:provider_call"
        and (record.get("span") or {}).get("end") is not None
    ]
    assert finished
    assert finished[-1]["span"]["status"] == "error"
    assert finished[-1]["span"]["output"]["error_type"] == "RuntimeError"
