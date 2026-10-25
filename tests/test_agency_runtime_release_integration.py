from pathlib import Path
import json

from portable.agency_observability import Dataset, DatasetItem, LocalExporter, Tracer
from portable.agency_regression_loop import RegressionPlan
from portable.agency_release_lifecycle import ArtifactStore
from portable.agency_runtime import TaskProfile, execute


def test_execute_applies_promote_to_artifact(tmp_path: Path):
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("candidate", encoding="utf-8")
    store = ArtifactStore(tmp_path / "releases")
    dataset = Dataset("smoke", "1", (DatasetItem("case", "ok"),))
    plan = RegressionPlan(
        dataset=dataset,
        execute_case=lambda trace, value: value,
        judge=lambda trace, item, output: {"quality": 1.0},
    )
    task = TaskProfile("t-release", "ship artifact")

    def worker(task, assignments):
        return {
            "deliverables": [str(artifact)],
            "verification": ["smoke"],
            "dimensions": {
                "correctness": 25, "completeness": 15, "evidence": 15,
                "verification": 15, "scope_discipline": 10,
                "security_and_safety": 10, "clarity": 5, "maintainability": 5,
            },
            "hard_gates": {"tests": True},
            "evidence": [{"source": "test", "claim": "artifact verified"}],
        }

    result = execute(task, worker, regression_plan=plan, release_store=store, release_artifact=str(artifact))
    assert result.promotion_decision.action == "promote"
    assert result.release_state.action == "promote"
    assert store.state()["current"]["artifact_id"] == "t-release"

def test_execute_binds_actual_artifact_transition_to_release_trace(tmp_path: Path):
    artifact = tmp_path / "artifact-traced.txt"
    artifact.write_text("candidate", encoding="utf-8")
    store = ArtifactStore(tmp_path / "releases-traced")
    dataset = Dataset("smoke-traced", "1", (DatasetItem("case", "ok"),))
    plan = RegressionPlan(
        dataset=dataset,
        execute_case=lambda trace, value: value,
        judge=lambda trace, item, output: {"quality": 1.0},
    )
    trace_path = tmp_path / "runtime-release-trace.jsonl"
    tracer = Tracer(LocalExporter(trace_path, enabled=True))
    task = TaskProfile("t-release-traced", "ship artifact")

    def worker(task, assignments):
        return {
            "deliverables": [str(artifact)],
            "verification": ["smoke"],
            "dimensions": {
                "correctness": 25, "completeness": 15, "evidence": 15,
                "verification": 15, "scope_discipline": 10,
                "security_and_safety": 10, "clarity": 5, "maintainability": 5,
            },
            "hard_gates": {"tests": True},
            "evidence": [{"source": "test", "claim": "artifact verified"}],
        }

    result = execute(
        task, worker, regression_plan=plan, release_store=store,
        release_artifact=str(artifact), tracer=tracer,
    )
    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    spans = [
        record["span"] for record in records
        if record.get("event_type") == "span_started"
        and (record.get("span") or {}).get("name") in {"release_transition", "release_transition:promote"}
        and record.get("trace_id") == result.trace_id
    ]
    by_name = {span["name"]: span for span in spans}
    assert set(by_name) == {"release_transition", "release_transition:promote"}
    assert by_name["release_transition:promote"]["parent_id"] == by_name["release_transition"]["span_id"]
    assert by_name["release_transition:promote"]["phase"] == "release"
