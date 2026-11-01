"""Durable response delivery metrics remain content-free and digest-bound."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable.progress_journal import ProgressJournal
from portable.response_delivery_metrics import (
    aggregate_delivery_metrics, record_chunk_ack_measurement,
    record_chunk_offer, record_delivery_failure, record_delivery_metrics,
)
from portable.response_delivery_supervisor import ResponseDeliverySupervisor


class ResponseDeliveryMetricsTests(unittest.TestCase):
    def test_metrics_are_durable_and_aggregate_without_response_content(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ProgressJournal(Path(temp) / "progress.jsonl")
            journal.record("task-1", "started", state="running", stage="execution")
            digest = "a" * 64
            record_delivery_metrics(
                journal, "task-1", digest,
                total_bytes=9000, total_chunks=3,
                peak_in_flight_bytes=4096, backpressure_wait_ms=24.5,
                resumed_from_chunk=1, completion_digest=digest,
            )
            record_delivery_metrics(
                journal, "task-2", "b" * 64,
                total_bytes=500, total_chunks=1,
                peak_in_flight_bytes=500, backpressure_wait_ms=4.0,
                resumed_from_chunk=0, failure_reason="receiver_exception",
            )
            events = journal._events()
            metrics_events = [row for row in events if row.get("event") == "response_delivery_metrics"]
            self.assertEqual(2, len(metrics_events))
            self.assertNotIn("secret response body", " ".join(row.get("message", "") for row in metrics_events))
            self.assertTrue(any(row.get("evidence_ref") == "sha256:" + digest for row in metrics_events))
            result = aggregate_delivery_metrics(events)
            self.assertEqual(2, result["measured_deliveries"])
            self.assertEqual(9500, result["total_bytes"])
            self.assertEqual(4, result["total_chunks"])
            self.assertEqual(4096, result["peak_in_flight_bytes"])
            self.assertEqual(28.5, result["backpressure_wait_ms"])
            self.assertEqual(1, result["resume_count"])
            self.assertEqual(["receiver_exception"], result["failure_reasons"])
            self.assertEqual([digest], result["completion_digests"])

    def test_aggregator_ignores_malformed_or_untrusted_metric_rows(self):
        rows = [
            {"event": "response_delivery_metrics", "task_id": "task-a", "message": "[]"},
            {"event": "response_delivery_metrics", "task_id": "task-b", "message": "{not-json"},
            {"event": "response_delivery_metrics", "task_id": "task-c",
             "message": '{"schema_version":"auren-response-delivery-metrics-v1","response_id":"x","total_bytes":"bad"}'},
        ]
        result = aggregate_delivery_metrics(rows)
        self.assertEqual(0, result["measured_deliveries"])
        self.assertEqual([], result["completion_digests"])



    def test_http_offer_ack_metrics_are_idempotent_and_content_free(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ProgressJournal(Path(temp) / "progress.jsonl")
            journal.record("http-task", "started", state="running")
            response_id = "e" * 64
            chunk_digest = "f" * 64
            record_chunk_offer(
                journal, "http-task", response_id,
                chunk_index=1, chunk_sha256=chunk_digest,
                chunk_bytes=1200, total_bytes=2400, total_chunks=2,
            )
            import time
            time.sleep(0.005)
            first_wait = record_chunk_ack_measurement(
                journal, "http-task", response_id,
                chunk_index=1, chunk_sha256=chunk_digest, chunk_bytes=1200,
            )
            duplicate_wait = record_chunk_ack_measurement(
                journal, "http-task", response_id,
                chunk_index=1, chunk_sha256=chunk_digest, chunk_bytes=1200,
            )
            record_delivery_failure(journal, "http-task", response_id, "shared_budget_exhausted")
            events = journal._events()
            self.assertEqual(first_wait, duplicate_wait)
            self.assertEqual(1, sum(
                row.get("event") == "response_delivery_chunk_measurement" for row in events
            ))
            metrics = aggregate_delivery_metrics(events)
            self.assertEqual(1200, metrics["peak_in_flight_bytes"])
            self.assertGreaterEqual(metrics["backpressure_wait_ms"], 0)
            self.assertIn("shared_budget_exhausted", metrics["failure_reasons"])

    def test_supervisor_exposes_durable_measurements_to_control_center(self):
        class Controller:
            def __init__(self, path):
                self.db_path = Path(path)
            def _load_handler(self, _name):
                raise KeyError("not registered")
            def register_handler(self, *_args, **_kwargs):
                pass

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = root / "controller.sqlite3"
            with sqlite3.connect(db_path) as db:
                db.execute("CREATE TABLE tasks (id TEXT, state TEXT, work_key TEXT)")
                db.execute(
                    "INSERT INTO tasks(id,state,work_key) VALUES(?,?,?)",
                    ("task-1", "running", "dashboard-response:response-1"),
                )
            journal = ProgressJournal(root / "progress.jsonl")
            journal.record("task-1", "started", state="running")
            digest = "d" * 64
            record_delivery_metrics(
                journal, "task-1", digest,
                total_bytes=2048, total_chunks=2,
                peak_in_flight_bytes=1024, backpressure_wait_ms=15.0,
                resumed_from_chunk=1, completion_digest=digest,
            )
            supervisor = ResponseDeliverySupervisor(Controller(db_path), root / "progress.jsonl")
            metrics = supervisor.metrics_snapshot()
            self.assertEqual(2048, metrics["measurements"]["total_bytes"])
            self.assertEqual(2, metrics["measurements"]["total_chunks"])
            self.assertEqual(1024, metrics["measurements"]["peak_in_flight_bytes"])
            self.assertEqual(15.0, metrics["measurements"]["backpressure_wait_ms"])
            self.assertEqual(1, metrics["measurements"]["resume_count"])
            self.assertEqual([digest], metrics["measurements"]["completion_digests"])

    def test_metrics_reject_mismatched_completion_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            journal = ProgressJournal(Path(temp) / "progress.jsonl")
            journal.record("task-1", "started", state="running")
            with self.assertRaisesRegex(ValueError, "match the response identity"):
                record_delivery_metrics(
                    journal, "task-1", "a" * 64,
                    total_bytes=1, total_chunks=1,
                    peak_in_flight_bytes=1, backpressure_wait_ms=0,
                    resumed_from_chunk=0, completion_digest="b" * 64,
                )


if __name__ == "__main__":
    unittest.main()
