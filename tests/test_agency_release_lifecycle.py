from pathlib import Path
import json

from portable.agency_observability import LocalExporter, Tracer

from portable.agency_release_lifecycle import ArtifactStore
from portable.agency_regression_loop import PromotionDecision


def test_shadow_canary_promote_and_rollback(tmp_path: Path):
    source1 = tmp_path / "a.txt"
    source2 = tmp_path / "b.txt"
    source1.write_text("one", encoding="utf-8")
    source2.write_text("two", encoding="utf-8")
    store = ArtifactStore(tmp_path / "release")
    a = store.stage(source1, "v1")
    assert store.stage(source1, "v1-repeat").digest == a.digest
    b = store.stage(source2, "v2")

    shadow = store.apply_decision(PromotionDecision("shadow", "quality", 0.8, True, "r1"), a)
    assert shadow.action == "shadow"
    assert store.state()["current"] is None

    canary = store.apply_decision(PromotionDecision("canary", "staged", 0.95, True, "r2"), b)
    assert canary.action == "canary"
    assert store.state()["canary"]["artifact_id"] == "v2"
    assert store.state()["current"] is None

    promoted = store.apply_decision(PromotionDecision("promote", "passed", 0.99, True, "r3"), a)
    assert promoted.action == "promote"
    assert store.state()["current"]["artifact_id"] == "v1"

    promoted2 = store.apply_decision(PromotionDecision("promote", "passed", 0.99, True, "r4"), b)
    assert promoted2.action == "promote"
    assert store.state()["current"]["artifact_id"] == "v2"

    rolled = store.apply_decision(PromotionDecision("rollback", "regression failed", 0.1, False, "r5"))
    assert rolled.action == "rollback"
    assert store.state()["current"]["artifact_id"] == "v1"


def test_release_transition_emits_parented_phase_span(tmp_path: Path):
    source = tmp_path / "candidate.txt"
    source.write_text("candidate", encoding="utf-8")
    store = ArtifactStore(tmp_path / "release")
    artifact = store.stage(source, "candidate-v1")
    trace_path = tmp_path / "release-trace.jsonl"
    tracer = Tracer(LocalExporter(trace_path, enabled=True))
    trace = tracer.start("release-run", run_id="release-run", task_id="release-task")
    parent = trace.span("supervisor", phase="supervisor")

    state = store.transition(
        "shadow", artifact, "evaluate candidate",
        context_evidence_digest="context-digest",
        trace=trace, parent_span_id=parent.span_id,
    )
    assert state.action == "shadow"
    tracer.end(trace)

    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    started = [
        record["span"] for record in records
        if record.get("event_type") == "span_started"
        and (record.get("span") or {}).get("name") == "release_transition:shadow"
    ]
    assert len(started) == 1
    assert started[0]["parent_id"] == parent.span_id
    assert started[0]["phase"] == "release"
    assert started[0]["actor_id"] == "auren.agency_release_lifecycle"
    assert started[0]["metadata"]["artifact_digest"] == artifact.digest
    assert "evaluate candidate" not in json.dumps(started[0])
    finished = [
        record["span"] for record in records
        if record.get("event_type") == "span_updated"
        and (record.get("span") or {}).get("name") == "release_transition:shadow"
        and (record.get("span") or {}).get("end") is not None
    ]
    assert finished[-1]["status"] == "ok"
    assert finished[-1]["output"]["transition_applied"] is True


def test_rejected_release_transition_emits_error_span(tmp_path: Path):
    trace_path = tmp_path / "release-error-trace.jsonl"
    tracer = Tracer(LocalExporter(trace_path, enabled=True))
    trace = tracer.start("release-error-run", run_id="release-error-run", task_id="release-task")

    try:
        ArtifactStore(tmp_path / "release").transition(
            "promote", None, "missing candidate", trace=trace,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("invalid promotion must fail closed")
    tracer.end(trace)

    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    finished = [
        record["span"] for record in records
        if record.get("event_type") == "span_updated"
        and (record.get("span") or {}).get("name") == "release_transition:promote"
        and (record.get("span") or {}).get("end") is not None
    ]
    assert finished[-1]["status"] == "error"
    assert finished[-1]["output"]["transition_applied"] is False


def test_rollback_without_active_artifact_is_recorded(tmp_path: Path):
    store = ArtifactStore(tmp_path / "release")
    state = store.transition("rollback", None, "failed candidate")
    assert state.action == "rollback"
    assert state.artifact_id is None


def test_channel_state_detects_tampered_content(tmp_path: Path):
    source = tmp_path / "a.txt"
    source.write_text("one", encoding="utf-8")
    store = ArtifactStore(tmp_path / "release")
    artifact = store.stage(source, "v1")
    store.transition("promote", artifact, "verified")
    payload = Path(artifact.path) / source.name
    payload.write_text("tampered", encoding="utf-8")
    try:
        store.state()
    except RuntimeError as exc:
        assert "digest" in str(exc)
    else:
        raise AssertionError("tampered artifact must not remain trusted")
