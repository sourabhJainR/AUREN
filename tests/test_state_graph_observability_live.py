from __future__ import annotations

import json
from pathlib import Path

from portable.agency_observability import LocalExporter, Tracer
from portable.orchestration import Node, NodeKind, RunStatus
from portable.runtime_recovery import ExecutionBudgetExceeded
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
        raise ExecutionBudgetExceeded("synthetic hard stop")

    graph = StateGraph.from_nodes([Node("provider_call", NodeKind.AGENT, fail)])
    tracer = Tracer(LocalExporter(path, enabled=True))

    try:
        graph.run("task-failed", "run failing node", tracer=tracer)
    except ExecutionBudgetExceeded:
        pass
    else:
        raise AssertionError("hard budget exhaustion must stop the supervisor")
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


def test_partial_dependency_acceptance_is_bound_to_canonical_trace(tmp_path: Path) -> None:
    path = tmp_path / "partial-dependency-trace.jsonl"

    def fail_optional_news(_state):
        raise RuntimeError("api_key=must-not-leak")

    graph = StateGraph.from_nodes([
        Node("fundamentals", NodeKind.AGENT, lambda _state: {"price": 10}),
        Node("news", NodeKind.AGENT, fail_optional_news, critical=False),
        Node(
            "synthesis", NodeKind.JOIN,
            lambda state: {
                "partial": bool(state["auren_partial_dependencies"]),
                "price": state["fundamentals"]["price"],
            },
            depends_on=("fundamentals", "news"),
            evaluator=lambda output: output["partial"] and output["price"] == 10,
            allow_partial_dependencies=True,
        ),
    ])
    tracer = Tracer(LocalExporter(path, enabled=True))

    result = graph.run("task-partial", "synthesize partial stock evidence", tracer=tracer)

    assert result.status == RunStatus.ACCEPTED
    assert result.results["synthesis"].partial_dependencies == ("news",)
    records = _trace_records(path)
    synthesis = [
        record for record in records
        if record.get("event_type") == "span_updated"
        and (record.get("span") or {}).get("name") == "supervisor_node:synthesis"
    ]
    assert synthesis
    assert synthesis[-1]["span"]["metadata"]["partial_dependencies"] == ["news"]
    assert synthesis[-1]["span"]["output"]["partial_dependencies"] == ["news"]
    assert "api_key=must-not-leak" not in path.read_text(encoding="utf-8")


def test_evaluator_and_repair_spans_are_nested_under_each_real_attempt(tmp_path: Path) -> None:
    path = tmp_path / "evaluation-repair-traces.jsonl"
    attempts = []

    def run(_state):
        attempts.append(len(attempts) + 1)
        return {"attempt": attempts[-1]}

    graph = StateGraph.from_nodes([
        Node(
            "implementation",
            NodeKind.AGENT,
            run,
            max_attempts=2,
            evaluator=lambda output: output["attempt"] == 2,
            repair=lambda output, _state: output,
        ),
    ])
    tracer = Tracer(LocalExporter(path, enabled=True))

    result = graph.run("task-evaluation", "trace evaluator and repair lifecycle", tracer=tracer)

    assert result.status == RunStatus.ACCEPTED
    assert result.results["implementation"].attempts == 2
    assert result.results["implementation"].repair_count == 1
    records = _trace_records(path)
    root = next(
        record["span"]["span_id"] for record in records
        if record.get("event_type") == "span_started"
        and (record.get("span") or {}).get("name") == "supervisor"
    )
    started = {
        record["span"]["metadata"]["attempt_number"]: record["span"]["span_id"]
        for record in records
        if record.get("event_type") == "span_started"
        and (record.get("span") or {}).get("name") == "node_execution:implementation"
    }
    evaluations = [
        record["span"] for record in records
        if record.get("event_type") == "span_updated"
        and (record.get("span") or {}).get("name") == "node_evaluation:implementation"
    ]
    repairs = [
        record["span"] for record in records
        if record.get("event_type") == "span_updated"
        and (record.get("span") or {}).get("name") == "node_repair:implementation"
    ]
    assert [item["status"] for item in evaluations] == ["rejected", "ok"]
    assert [item["parent_id"] for item in evaluations] == [root, root]
    assert len(repairs) == 1
    assert repairs[0]["parent_id"] == root
    terminal = [record for record in records if record.get("event_type") == "trace_finished"]
    assert terminal
    coverage = terminal[-1]["metadata"]["lifecycle_coverage"]
    assert coverage["configured_evaluator_count"] == 1
    assert coverage["observed_evaluator_span_count"] == 2
    assert coverage["configured_repair_count"] == 1
    assert coverage["observed_repair_span_count"] == 1
    assert coverage["external_learning_persistence"] == "not_instrumented_by_state_graph"


def test_evaluator_exception_is_visible_without_leaking_exception_text(tmp_path: Path) -> None:
    path = tmp_path / "evaluator-error-traces.jsonl"

    def reject_with_secret(_output):
        raise ValueError("api_key=must-not-leak")

    graph = StateGraph.from_nodes([
        Node("verify", NodeKind.AGENT, lambda _state: {"ready": True}, evaluator=reject_with_secret),
    ])
    tracer = Tracer(LocalExporter(path, enabled=True))
    result = graph.run("task-evaluator-error", "trace evaluator exception", tracer=tracer)

    assert result.status == RunStatus.FAILED
    records = _trace_records(path)
    errors = [
        record["span"] for record in records
        if record.get("event_type") == "span_updated"
        and (record.get("span") or {}).get("name") == "node_evaluation:verify"
    ]
    assert errors
    assert errors[-1]["status"] == "error"
    assert errors[-1]["terminal_reason"] == "ValueError"
    assert "api_key=must-not-leak" not in path.read_text(encoding="utf-8")
    terminal = [record for record in records if record.get("event_type") == "trace_finished"]
    assert terminal[-1]["metadata"]["lifecycle_coverage"]["observed_evaluator_span_count"] == 1
