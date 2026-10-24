from pathlib import Path
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from portable.agency_observability import Dataset, DatasetItem, LocalExporter, PromptRegistry, Tracer, retrieval_scores, run_experiment
from portable.agency_runtime import EvidenceItem, TaskProfile, execute


def test_trace_export_is_local_and_redacts_secrets(tmp_path: Path):
    exporter = LocalExporter(tmp_path / "traces.jsonl", enabled=True)
    tracer = Tracer(exporter)
    trace = tracer.start("task", {"token": "secret-value"})
    span = trace.span("worker", "agent", {"api_key": "abc123"})
    span.score("quality", 0.9, "good")
    span.finish({"authorization": "Bearer abc"})
    tracer.end(trace)
    text = (tmp_path / "traces.jsonl").read_text()
    assert "secret-value" not in text
    assert "abc123" not in text
    assert "Bearer abc" not in text
    assert trace.as_dict()["spans"][0]["scores"][0]["value"] == 0.9
    event = json.loads(text.splitlines()[-1])
    assert event["schema_version"] == 1
    assert event["task_id"] is None
    assert len(event["event_digest"]) == 64


def test_prompt_registry_versions_and_promotion():
    registry = PromptRegistry()
    v1 = registry.register("planner", "1", "plan: {task}")
    v2 = registry.register("planner", "2", "plan safely: {task}")
    assert registry.get("planner", "1").digest == v1.digest
    assert registry.promote("planner", "2") == v2
    assert registry.get("planner").version == "2"


def test_dataset_experiment_is_reproducible():
    dataset = Dataset("coding", "1", (DatasetItem("a", "A"), DatasetItem("b", "B")))
    result = run_experiment(dataset, lambda value: value.upper(), lambda item, output: {"correctness": float(output == item.expected)})
    assert result.passed
    assert result.scores["correctness"] == 1.0
    assert len(result.dataset_digest) == 64


def test_retrieval_scores():
    scores = retrieval_scores(["a.py", "b.py"], ["a.py", "c.py"])
    assert scores == {"precision": 0.5, "recall": 0.5}


def test_execute_emits_trace_id_without_changing_contract(tmp_path: Path):
    tracer = Tracer(LocalExporter(tmp_path / "traces.jsonl", enabled=True))
    task = TaskProfile("t1", "fix login", evidence_required=("source",))

    def worker(task, assignments):
        return {
            "deliverables": ["patch"],
            "verification": ["tests pass"],
            "dimensions": {"correctness": 25, "completeness": 15, "evidence": 15, "verification": 15, "scope_discipline": 10, "security_and_safety": 10, "clarity": 5, "maintainability": 5},
            "hard_gates": {"tests": True},
            "evidence": [EvidenceItem("test", "tests pass")],
        }

    result = execute(task, worker, tracer=tracer)
    assert result.trace_id
    assert result.receipt is not None and result.receipt.passed
    assert "task_quality" in (tmp_path / "traces.jsonl").read_text()


def test_execute_correlates_trace_with_regression(tmp_path: Path):
    tracer = Tracer(LocalExporter(tmp_path / "traces.jsonl", enabled=True))
    task = TaskProfile("t13", "fix login", evidence_required=("source",))

    def worker(task, assignments):
        return {
            "deliverables": ["patch"],
            "verification": ["tests pass"],
            "dimensions": {"correctness": 25, "completeness": 15, "evidence": 15, "verification": 15, "scope_discipline": 10, "security_and_safety": 10, "clarity": 5, "maintainability": 5},
            "hard_gates": {"tests": True},
            "evidence": [EvidenceItem("test", "tests pass")],
        }

    regression = run_experiment(
        Dataset("coding-regression", "13", (DatasetItem("fix login", "ok"),)),
        lambda value: "ok",
        lambda item, output: {"correctness": float(output == item.expected)},
    )
    result = execute(task, worker, tracer=tracer, regression_id="reg-t13", regression_result=regression)
    assert result.regression_link is not None
    assert result.regression_link.trace_id == result.trace_id
    assert result.regression_link.regression_id == "reg-t13"
    assert result.regression_link.passed
    assert "regression_pass" in (tmp_path / "traces.jsonl").read_text()



def test_span_parentage_is_explicit_and_invalid_parents_are_rejected():
    trace = Tracer().start("nested")
    root = trace.span("root")
    child = trace.span("child", parent_id=root.span_id)
    sibling = trace.span("sibling")
    assert child.parent_id == root.span_id
    assert sibling.parent_id is None
    with pytest.raises(ValueError, match="parent_id"):
        trace.span("orphan", parent_id="missing-span")


def test_prompt_versions_cannot_be_rewritten():
    registry = PromptRegistry()
    registry.register("planner", "1", "plan carefully")
    with pytest.raises(ValueError, match="immutable"):
        registry.register("planner", "1", "skip the checks")


def test_empty_or_inconsistently_scored_datasets_never_pass():
    with pytest.raises(ValueError, match="empty datasets"):
        run_experiment(Dataset("empty", "1", ()), lambda value: value, lambda item, output: {"quality": 1.0})
    dataset = Dataset("inconsistent", "1", (DatasetItem("a", "a"), DatasetItem("b", "b")))
    def judge(item, output):
        return {"quality": 1.0} if item.input == "a" else {}
    with pytest.raises(ValueError, match="no evaluation dimensions"):
        run_experiment(dataset, lambda value: value, judge)


def test_score_reasons_and_bearer_tokens_are_redacted(tmp_path: Path):
    exporter = LocalExporter(tmp_path / "traces.jsonl", enabled=True)
    tracer = Tracer(exporter)
    trace = tracer.start("privacy")
    span = trace.span("provider", input={"prompt": "Authorization: Bearer secret-token"})
    span.score("quality", 0.8, "api_key=topsecret")
    span.finish({"note": "Bearer abcdefghijklmnop"})
    tracer.end(trace)
    text = (tmp_path / "traces.jsonl").read_text()
    assert "secret-token" not in text
    assert "topsecret" not in text
    assert "abcdefghijklmnop" not in text
    assert "[REDACTED]" in text


def test_concurrent_span_creation_preserves_unique_ids():
    trace = Tracer().start("concurrent")
    with ThreadPoolExecutor(max_workers=8) as pool:
        spans = list(pool.map(lambda index: trace.span(f"span-{index}"), range(100)))
    ids = [span.span_id for span in spans]
    assert len(ids) == len(set(ids)) == 100



def test_export_failure_does_not_change_canonical_trace_outcome():
    class BrokenExporter:
        def export(self, trace):
            raise OSError("telemetry unavailable")

    tracer = Tracer(BrokenExporter())
    trace = tracer.start("execution", {"task_id": "task-1"})
    ended = tracer.end(trace, "ok")
    assert ended.status == "ok"
    assert ended.metadata["observability_export_status"] == "failed"
    assert ended.metadata["observability_export_error"] == "OSError"
