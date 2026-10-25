from pathlib import Path
import json

from portable.agency_observability import Dataset, DatasetItem, LocalExporter, Tracer
from portable.agency_regression_loop import RegressionPlan
from portable.agency_release_lifecycle import ArtifactStore
from portable.agency_runtime import TaskProfile, execute
from portable.engineering_personas import AgentPersona, PersonaRoster, PersonaReview, PersonaWorkflow, EngineeringPlan


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
            "artifact_refs": [str(artifact)],
            "verification": ["smoke"],
            "dimensions": {
                "correctness": 25, "completeness": 15, "evidence": 15,
                "verification": 15, "scope_discipline": 10,
                "security_and_safety": 10, "clarity": 5, "maintainability": 5,
            },
            "hard_gates": {"tests": True},
            "evidence": [{"source": "test", "claim": "artifact verified"}],
        }

    roster = PersonaRoster(
        planner=AgentPersona("planner", "planner-1", "planning persona"),
        plan_reviewer=AgentPersona("plan_reviewer", "plan-reviewer-1", "independent plan review"),
        implementer=AgentPersona("implementer", "implementer-1", "implementation persona"),
        implementation_reviewer=AgentPersona("implementation_reviewer", "implementation-reviewer-1", "independent implementation review"),
    )

    def make_plan(_task, assignments):
        return EngineeringPlan(
            goal="release verified candidate",
            scope=("artifact",),
            assumptions=("candidate is isolated",),
            acceptance_criteria=("regression passes",),
            dependencies=("test suite",),
            alternatives_considered=("hold release",),
            risks_and_mitigations=("rollback to previous artifact",),
            test_strategy=("pinned regression dataset",),
            rollback_plan=("restore prior promoted artifact",),
            budget={"max_runs": 1},
            source_revision="test-source-revision",
            decision_rationale="regression evidence required",
            selected_specialists=(assignments[0].specialist,),
        )

    def approve_plan(_task, _plan):
        return PersonaReview("plan-reviewer-1", "planner-1", True, evidence_refs=("review:plan",), dimensions={"correctness": 0.9})

    def approve_implementation(_task, _plan, _raw):
        return PersonaReview("implementation-reviewer-1", "implementer-1", True, evidence_refs=("review:implementation",), dimensions={"correctness": 0.9})

    workflow = PersonaWorkflow(roster, make_plan, approve_plan, approve_implementation)
    trace_path = tmp_path / "runtime-release-trace.jsonl"
    tracer = Tracer(LocalExporter(trace_path, enabled=True))
    result = execute(
        task, worker, regression_plan=plan, release_store=store,
        release_artifact=str(artifact), persona_workflow=workflow, tracer=tracer,
    )
    assert result.promotion_decision.action == "promote"
    assert result.release_state.action == "promote"
    assert store.state()["current"]["artifact_id"] == "t-release"

    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    spans = [
        record["span"] for record in records
        if record.get("event_type") == "span_started"
        and (record.get("span") or {}).get("name") in {"release_transition", "release_transition:promote"}
    ]
    assert {span["name"] for span in spans} == {"release_transition", "release_transition:promote"}
    outer = next(span for span in spans if span["name"] == "release_transition")
    inner = next(span for span in spans if span["name"] == "release_transition:promote")
    assert inner["parent_id"] == outer["span_id"]
    assert inner["phase"] == "release"
    assert inner["actor_id"] == "auren.agency_release_lifecycle"
    assert inner["metadata"]["artifact_digest"] == result.release_state.digest
    assert "reason" not in outer.get("input", {})
    assert "reason" not in inner.get("metadata", {})
    serialized_trace = json.dumps(records)
    assert result.promotion_decision.reason not in serialized_trace
