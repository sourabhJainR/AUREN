import json

import pytest

from portable.agency_observability import Dataset, DatasetItem, Tracer
from portable.agency_observability_ops import (
    CalibrationSample,
    DatasetRegistry,
    DriftPolicy,
    MetricObservation,
    ReviewedTraceExample,
    SQLiteDatasetRegistry,
    authorize_calibrated_guardrail,
    calibrate_evaluator,
    evaluate_drift,
    observability_snapshot,
    reviewed_trace_to_dataset_item,
)


def failed_trace():
    trace = Tracer().start("failure", {"task_id": "task-1"})
    span = trace.span("implementation", phase="implementation")
    span.finish({"summary": "bounded failure"}, "error")
    return Tracer().end(trace, "error")


def test_failed_trace_requires_review_and_minimized_regression_case():
    trace = failed_trace()
    review = ReviewedTraceExample(
        reviewer_id="reviewer-1",
        evidence_ref="review:case-1",
        minimized_input={"request": "minimal reproduction"},
        expected_behavior={"must": "reject unsafe input"},
        approved=True,
    )
    item = reviewed_trace_to_dataset_item(trace, review)
    assert item.metadata["source_trace_id"] == trace.trace_id
    assert item.metadata["source_event_digest"]
    assert item.metadata["reviewed"] is True
    assert "raw prompt" not in json.dumps(item.input).lower()


def test_sealed_holdout_and_unapproved_trace_cannot_be_promoted():
    trace = failed_trace()
    base = dict(
        reviewer_id="reviewer-1",
        evidence_ref="review:evidence",
        minimized_input={"case": 1},
        expected_behavior={"expected": "reject"},
        approved=True,
    )
    with pytest.raises(ValueError, match="sealed holdout"):
        reviewed_trace_to_dataset_item(trace, ReviewedTraceExample(**base, sealed_holdout=True))
    with pytest.raises(ValueError, match="approval"):
        reviewed_trace_to_dataset_item(trace, ReviewedTraceExample(**{**base, "approved": False}))


def test_dataset_registry_rejects_mutation_of_existing_revision():
    registry = DatasetRegistry()
    original = Dataset("security", "1", (DatasetItem("a", "reject"),))
    registry.register(original)
    assert registry.register(original).digest == original.digest
    with pytest.raises(ValueError, match="immutable"):
        registry.register(Dataset("security", "1", (DatasetItem("a", "allow"),)))


def test_calibration_requires_training_and_independent_validation():
    insufficient = calibrate_evaluator(
        [CalibrationSample(0.9, True, "train:1")],
        [CalibrationSample(0.1, False, "valid:1")],
        min_training=2,
        min_validation=2,
    )
    assert insufficient.status == "unscored"
    train = tuple(
        [CalibrationSample(0.1, False, f"train:bad:{i}") for i in range(20)]
        + [CalibrationSample(0.9, True, f"train:good:{i}") for i in range(20)]
    )
    valid = tuple(
        [CalibrationSample(0.1, False, f"valid:bad:{i}") for i in range(10)]
        + [CalibrationSample(0.9, True, f"valid:good:{i}") for i in range(10)]
    )
    calibrated = calibrate_evaluator(train, valid, min_training=30, min_validation=20)
    assert calibrated.status == "calibrated"
    assert calibrated.threshold is not None
    assert calibrated.eligible_for_guardrail is False
    authorization = authorize_calibrated_guardrail(
        "correctness", calibrated, reviewer_id="eval-reviewer", evidence_ref="calibration:report"
    )
    assert authorization.threshold == calibrated.threshold


def test_drift_alerts_on_quality_shift_and_safety_floor():
    points = [
        MetricObservation("correctness", "coding", 0.95, f"run-{i}", f"evidence:{i}", float(i))
        for i in range(20)
    ] + [
        MetricObservation("correctness", "coding", 0.60, f"run-{i}", f"evidence:{i}", float(i))
        for i in range(20, 30)
    ]
    report = evaluate_drift(points, DriftPolicy(baseline_count=20, recent_count=10, safety_floor=0.8))
    assert report["status"] == "alert"
    assert "distribution_shift" in report["alert_reasons"]
    assert "safety_floor_breached" in report["alert_reasons"]


def test_dashboard_snapshot_never_exposes_raw_span_payloads(tmp_path):
    trace = Tracer().start("failure", {"task_id": "task-1"})
    span = trace.span("implementation", phase="implementation")
    span.finish({"summary": "bounded failure"}, "error")
    sensitive = trace.span("sensitive", input={"prompt": "do not expose"})
    sensitive.finish({"output": "private"}, "ok")
    Tracer().end(trace, "error")
    trace_path = tmp_path / "traces.jsonl"
    trace_path.write_text(json.dumps(trace.as_dict()) + "\n", encoding="utf-8")
    snapshot = observability_snapshot(trace_path)
    serialized = json.dumps(snapshot)
    assert snapshot["trace_count"] == 1
    assert snapshot["raw_payloads_exposed"] is False
    assert "do not expose" not in serialized
    assert "private" not in serialized



def test_sqlite_dataset_revisions_survive_reopen_and_are_immutable(tmp_path):
    path = tmp_path / "datasets.sqlite3"
    original = Dataset("reviewed", "1", (DatasetItem({"input": "minimal"}, {"expected": "reject"}, {"reviewed": True}),))
    first = SQLiteDatasetRegistry(path)
    first.register(original)
    reopened = SQLiteDatasetRegistry(path)
    assert reopened.get("reviewed", "1").digest == original.digest
    with pytest.raises(ValueError, match="immutable"):
        reopened.register(Dataset("reviewed", "1", (DatasetItem({"input": "changed"}, {"expected": "allow"}),)))
