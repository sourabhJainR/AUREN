"""Integration regressions for bounded acknowledged response transport."""
import tempfile
import unittest
from pathlib import Path

from portable.progress_journal import ProgressJournal
from portable.response_delivery_metrics import aggregate_delivery_metrics
from portable.response_delivery_transport import (
    DeliveryBlocked,
    DeliveryOutcomeUnknown,
    deliver_response,
)


class ResponseDeliveryTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = ProgressJournal(Path(self.temp.name) / "progress.jsonl")

    def tearDown(self):
        self.temp.cleanup()

    def start_task(self, task_id):
        self.journal.record(task_id, "delivery_started", state="running", stage="response-delivery")

    @staticmethod
    def ack(chunk):
        return {
            "response_id": chunk.response_id,
            "index": chunk.index,
            "chunk_sha256": chunk.sha256,
        }

    def test_delivers_with_one_chunk_backpressure_and_durable_acks(self):
        self.start_task("task-1")
        source = ("line with unicode 東京🙂\n" * 100)
        seen = []
        receipt = deliver_response(
            "task-1", source, receiver=lambda chunk: (seen.append(chunk), self.ack(chunk))[1],
            journal=self.journal, max_chunk_bytes=80,
        )
        self.assertEqual(receipt.acknowledged_chunks, receipt.total_chunks)
        self.assertEqual(receipt.peak_in_flight_bytes, max(c.utf8_bytes for c in seen))
        self.assertTrue(all(c.utf8_bytes <= 80 for c in seen))
        checkpoint = self.journal.response_delivery_checkpoint("task-1", receipt.response_id)
        self.assertEqual(checkpoint["acknowledged_index"], receipt.total_chunks)
        metrics = aggregate_delivery_metrics(self.journal._events())
        self.assertEqual(len(source.encode("utf-8")), metrics["total_bytes"])
        self.assertEqual(receipt.total_chunks, metrics["total_chunks"])
        self.assertEqual(receipt.peak_in_flight_bytes, metrics["peak_in_flight_bytes"])
        self.assertEqual([receipt.response_id], metrics["completion_digests"])

    def test_restart_resumes_after_last_acknowledged_chunk(self):
        self.start_task("task-2")
        source = "payload " * 100
        seen = []

        def receive(chunk):
            seen.append(chunk.index)
            return self.ack(chunk)

        with self.assertRaises(DeliveryBlocked):
            deliver_response(
                "task-2", source, receiver=receive, journal=self.journal,
                max_chunk_bytes=40,
                consume_shared_budget=lambda size: len(seen) < 2,
            )
        resumed = []
        receipt = deliver_response(
            "task-2", source,
            receiver=lambda chunk: (resumed.append(chunk.index), self.ack(chunk))[1],
            journal=self.journal, max_chunk_bytes=40,
        )
        self.assertEqual(resumed, list(range(3, receipt.total_chunks + 1)))
        self.assertEqual(receipt.resumed_from_chunk, 2)
        self.assertEqual(seen, [1, 2])
        metrics = aggregate_delivery_metrics(self.journal._events())
        self.assertEqual(1, metrics["resume_count"])
        self.assertGreater(metrics["total_chunks"], 0)

    def test_ambiguous_send_is_durably_blocked_from_automatic_replay(self):
        self.start_task("task-ambiguous")
        calls = []

        def disconnect(chunk):
            calls.append(chunk.index)
            raise OSError("connection dropped after send")

        with self.assertRaises(DeliveryOutcomeUnknown):
            deliver_response("task-ambiguous", "remote result",
                             receiver=disconnect, journal=self.journal)
        with self.assertRaises(DeliveryBlocked):
            deliver_response("task-ambiguous", "remote result",
                             receiver=lambda chunk: calls.append(chunk.index),
                             journal=self.journal)
        self.assertEqual(calls, [1])
        metrics = aggregate_delivery_metrics(self.journal._events())
        self.assertIn("receiver_exception", metrics["failure_reasons"])


    def test_metrics_write_failure_cannot_erase_unknown_outcome_fence(self):
        from unittest.mock import patch
        self.start_task("task-metrics-failure")
        with patch(
            "portable.response_delivery_transport.record_delivery_metrics",
            side_effect=OSError("metrics store unavailable"),
        ):
            with self.assertRaises(DeliveryOutcomeUnknown):
                deliver_response(
                    "task-metrics-failure", "remote result",
                    receiver=lambda chunk: (_ for _ in ()).throw(OSError("receiver disconnected")),
                    journal=self.journal,
                )
        events = self.journal._events()
        self.assertTrue(any(
            row.get("task_id") == "task-metrics-failure"
            and row.get("event") == "response_chunk_outcome_unknown"
            for row in events
        ))
        with self.assertRaises(DeliveryBlocked):
            deliver_response(
                "task-metrics-failure", "remote result",
                receiver=self.ack, journal=self.journal,
            )

    def test_metrics_write_failure_does_not_change_successful_delivery_receipt(self):
        from unittest.mock import patch
        self.start_task("task-metrics-success")
        with patch(
            "portable.response_delivery_transport.record_delivery_metrics",
            side_effect=OSError("metrics store unavailable"),
        ):
            receipt = deliver_response(
                "task-metrics-success", "complete payload",
                receiver=self.ack, journal=self.journal,
            )
        self.assertEqual(receipt.total_chunks, receipt.acknowledged_chunks)
        checkpoint = self.journal.response_delivery_checkpoint(
            "task-metrics-success", receipt.response_id,
        )
        self.assertEqual(receipt.total_chunks, checkpoint["acknowledged_index"])

    def test_mismatched_ack_blocks_without_advancing_cursor(self):
        self.start_task("task-3")
        with self.assertRaises(DeliveryOutcomeUnknown):
            deliver_response(
                "task-3", "important result",
                receiver=lambda chunk: {"response_id": chunk.response_id,
                                        "index": chunk.index,
                                        "chunk_sha256": "0" * 64},
                journal=self.journal,
            )
        self.assertEqual(
            self.journal.response_delivery_checkpoint(
                "task-3", __import__("hashlib").sha256(b"important result").hexdigest()
            )["acknowledged_index"], 0,
        )

    def test_cancel_and_shared_budget_stop_before_next_send(self):
        self.start_task("task-4")
        calls = []
        with self.assertRaises(DeliveryBlocked):
            deliver_response(
                "task-4", "x" * 200,
                receiver=lambda chunk: (calls.append(chunk.index), self.ack(chunk))[1],
                journal=self.journal, max_chunk_bytes=40,
                consume_shared_budget=lambda size: len(calls) == 0,
            )
        self.assertEqual(calls, [1])

    def test_total_byte_limit_fails_before_receiver(self):
        calls = []
        with self.assertRaises(DeliveryBlocked):
            deliver_response(
                "task-5", "x" * 100,
                receiver=lambda chunk: calls.append(chunk),
                journal=self.journal, max_total_bytes=10,
            )
        self.assertEqual(calls, [])




    def test_transport_consumes_the_same_checkpointed_crew_budget(self):
        from portable.runtime_recovery import RuntimeExecutionGuard
        self.start_task("task-shared")
        guard = RuntimeExecutionGuard(max_delivery_bytes=65)
        sent = []
        with self.assertRaises(DeliveryBlocked):
            deliver_response(
                "task-shared", "x" * 200,
                receiver=lambda chunk: (sent.append(chunk.index), self.ack(chunk))[1],
                journal=self.journal, max_chunk_bytes=40, execution_guard=guard,
            )
        self.assertEqual([1], sent)
        self.assertEqual(40, guard.delivery_bytes_used)
        resumed_guard = RuntimeExecutionGuard.resume(guard.snapshot())
        with self.assertRaises(DeliveryBlocked):
            deliver_response(
                "task-shared", "x" * 200,
                receiver=lambda chunk: sent.append(chunk.index),
                journal=self.journal, max_chunk_bytes=40, execution_guard=resumed_guard,
            )
        self.assertEqual([1], sent)

    def test_transport_rejects_two_budget_owners(self):
        from portable.runtime_recovery import RuntimeExecutionGuard
        self.start_task("task-budget-owner")
        with self.assertRaisesRegex(ValueError, "either the shared execution guard"):
            deliver_response(
                "task-budget-owner", "payload",
                receiver=lambda chunk: self.ack(chunk), journal=self.journal,
                execution_guard=RuntimeExecutionGuard(),
                consume_shared_budget=lambda size: True,
            )


    def test_slow_receiver_applies_backpressure_without_parallel_chunk_sends(self):
        import threading
        import time
        self.start_task("task-slow")
        active = 0
        peak_active = 0
        sent = []
        lock = threading.Lock()

        def slow_receiver(chunk):
            nonlocal active, peak_active
            with lock:
                active += 1
                peak_active = max(peak_active, active)
            time.sleep(0.01)
            sent.append(chunk.index)
            with lock:
                active -= 1
            return self.ack(chunk)

        receipt = deliver_response(
            "task-slow", "slow payload " * 100,
            receiver=slow_receiver, journal=self.journal, max_chunk_bytes=40,
        )
        self.assertTrue(receipt.acknowledged_chunks == receipt.total_chunks)
        self.assertEqual(1, peak_active)
        self.assertEqual(list(range(1, receipt.total_chunks + 1)), sent)
        metrics = aggregate_delivery_metrics(self.journal._events())
        self.assertGreater(metrics["backpressure_wait_ms"], 0)
        self.assertEqual(receipt.peak_in_flight_bytes, metrics["peak_in_flight_bytes"])


if __name__ == "__main__":
    unittest.main()
