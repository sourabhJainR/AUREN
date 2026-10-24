from pathlib import Path
import json

import unittest
import tempfile

from portable.agency_observability import Dataset, DatasetItem, LocalExporter, Tracer
from portable.agency_observability_ops import (
    CalibrationSample,
    DatasetRegistry,
    DriftPolicy,
    MetricObservation,
    ReviewedTraceExample,
    SQLiteDatasetRegistry,
    SQLiteObservabilityEvidenceStore,
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


class TestAgencyObservabilityOps(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temporary_directory.name)

    def tearDown(self):
        self._temporary_directory.cleanup()

    def test_failed_trace_requires_review_and_minimized_regression_case(self):
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



    def test_sealed_holdout_and_unapproved_trace_cannot_be_promoted(self):
        trace = failed_trace()
        base = dict(
            reviewer_id="reviewer-1",
            evidence_ref="review:evidence",
            minimized_input={"case": 1},
            expected_behavior={"expected": "reject"},
            approved=True,
        )
        with self.assertRaisesRegex(ValueError, "sealed holdout"):
            reviewed_trace_to_dataset_item(trace, ReviewedTraceExample(**base, sealed_holdout=True))
        with self.assertRaisesRegex(ValueError, "approval"):
            reviewed_trace_to_dataset_item(trace, ReviewedTraceExample(**{**base, "approved": False}))



    def test_dataset_registry_rejects_mutation_of_existing_revision(self):
        registry = DatasetRegistry()
        original = Dataset("security", "1", (DatasetItem("a", "reject"),))
        registry.register(original)
        assert registry.register(original).digest == original.digest
        with self.assertRaisesRegex(ValueError, "immutable"):
            registry.register(Dataset("security", "1", (DatasetItem("a", "allow"),)))



    def test_calibration_requires_training_and_independent_validation(self):
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
        store = SQLiteObservabilityEvidenceStore(self.tmp_path / "observability.sqlite3")
        persisted = store.authorize_guardrail(
            "correctness", calibrated, reviewer_id="eval-reviewer", evidence_ref="calibration:report"
        )
        assert persisted.threshold == calibrated.threshold



    def test_drift_alerts_on_quality_shift_and_safety_floor(self):
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
        store = SQLiteObservabilityEvidenceStore(self.tmp_path / "observability.sqlite3")
        alert_id = store.record_drift_alert(report)
        assert alert_id
        assert store.list_drift_alerts()[0]["status"] == "alert"



    def test_dashboard_snapshot_never_exposes_raw_span_payloads(self):
        trace = Tracer().start("failure", {"task_id": "task-1"})
        span = trace.span("implementation", phase="implementation")
        span.finish({"summary": "bounded failure"}, "error")
        sensitive = trace.span("sensitive", input={"prompt": "do not expose"})
        sensitive.finish({"output": "private"}, "ok")
        Tracer().end(trace, "error")
        trace_path = self.tmp_path / "traces.jsonl"
        trace_path.write_text(json.dumps(trace.as_dict()) + "\n", encoding="utf-8")
        snapshot = observability_snapshot(trace_path)
        serialized = json.dumps(snapshot)
        assert snapshot["trace_count"] == 1
        assert snapshot["raw_payloads_exposed"] is False
        assert "do not expose" not in serialized
        assert "private" not in serialized




    def test_sqlite_dataset_revisions_survive_reopen_and_are_immutable(self):
        path = self.tmp_path / "datasets.sqlite3"
        original = Dataset("reviewed", "1", (DatasetItem({"input": "minimal"}, {"expected": "reject"}, {"reviewed": True}),))
        first = SQLiteDatasetRegistry(path)
        first.register(original)
        reopened = SQLiteDatasetRegistry(path)
        assert reopened.get("reviewed", "1").digest == original.digest
        with self.assertRaisesRegex(ValueError, "immutable"):
            reopened.register(Dataset("reviewed", "1", (DatasetItem({"input": "changed"}, {"expected": "allow"}),)))




    def test_partial_trace_is_persisted_and_latest_snapshot_wins(self):
        path = self.tmp_path / "traces.jsonl"
        tracer = Tracer(LocalExporter(path, enabled=True))
        trace = tracer.start("resumable", {"task_id": "task-2"}, run_id="durable-run-2")
        span = trace.span("implementation", phase="implementation")
        span.finish({"summary": "completed span"}, "ok")
        partial = observability_snapshot(path)
        assert partial["trace_count"] == 1
        assert partial["recent_traces"][0]["status"] == "running"
        assert partial["recent_traces"][0]["run_id"] == "durable-run-2"
        assert partial["recent_traces"][0]["phases"][0]["status"] == "ok"
        tracer.end(trace, "ok")
        completed = observability_snapshot(path)
        assert completed["trace_count"] == 1
        assert completed["recent_traces"][0]["status"] == "ok"




    def test_snapshot_derives_quality_drift_without_exposing_trace_payloads(self):
        path = self.tmp_path / "traces.jsonl"
        tracer = Tracer(LocalExporter(path, enabled=True))
        values = [0.95] * 20 + [0.60] * 10
        for index, value in enumerate(values):
            trace = tracer.start(
                "quality-run",
                {"task_id": f"task-{index}", "domain": "coding", "risk": "normal"},
                run_id=f"run-{index}",
            )
            span = trace.span("quality", phase="verification")
            span.score("task_quality", value, "measured quality")
            span.finish({"summary": "scored"}, "ok")
            tracer.end(trace, "ok")
        snapshot = observability_snapshot(path)
        assert snapshot["trace_count"] == 30
        assert any(trend["metric"] == "task_quality" and trend["drift_status"] == "alert" for trend in snapshot["score_trends"])
        assert any(alert["metric"] == "task_quality" for alert in snapshot["live_drift_alerts"])
        assert snapshot["raw_payloads_exposed"] is False



    def test_tampered_incremental_trace_event_is_rejected(self):
        path = self.tmp_path / "tampered-events.jsonl"
        tracer = Tracer(LocalExporter(path, enabled=True))
        trace = tracer.start("integrity", {"task_id": "integrity-task"})
        span = trace.span("implementation", phase="implementation")
        span.finish({"summary": "completed"})
        records = path.read_text().splitlines()
        event_index = next(i for i, line in enumerate(records) if '"event_record_version":1' in line)
        event = json.loads(records[event_index])
        event["status"] = "failed"
        records[event_index] = json.dumps(event, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(records) + "\n")
        snapshot = observability_snapshot(path)
        assert snapshot["corrupt_line_count"] >= 1
        assert snapshot["status"] == "partial"
        assert snapshot["raw_payloads_exposed"] is False



    def test_missing_incremental_event_sequence_marks_trace_partial(self):
        path = self.tmp_path / "sequence-gap.jsonl"
        tracer = Tracer(LocalExporter(path, enabled=True))
        trace = tracer.start("sequence-gap", {"task_id": "gap-task"})
        span = trace.span("implementation", phase="implementation")
        span.finish({"summary": "finished"}, "ok")
        records = [json.loads(line) for line in path.read_text().splitlines()]
        filtered = [
            record for record in records
            if not (record.get("event_record_version") == 1 and record.get("event_sequence") == 1)
        ]
        path.write_text("\n".join(json.dumps(record, sort_keys=True, separators=(",", ":")) for record in filtered) + "\n")
        snapshot = observability_snapshot(path)
        assert snapshot["event_sequence_gap_count"] == 1
        assert snapshot["status"] == "partial"
