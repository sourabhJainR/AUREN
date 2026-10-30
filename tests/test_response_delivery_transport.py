"""Integration regressions for bounded acknowledged response transport."""
import tempfile
import unittest
from pathlib import Path

from portable.progress_journal import ProgressJournal
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

    @staticmethod
    def ack(chunk):
        return {
            "response_id": chunk.response_id,
            "index": chunk.index,
            "chunk_sha256": chunk.sha256,
        }

    def test_delivers_with_one_chunk_backpressure_and_durable_acks(self):
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

    def test_restart_resumes_after_last_acknowledged_chunk(self):
        source = "payload " * 100
        seen = []

        def disconnect_after_two(chunk):
            seen.append(chunk.index)
            if chunk.index == 3:
                raise OSError("connection dropped before acknowledgement")
            return self.ack(chunk)

        with self.assertRaises(DeliveryOutcomeUnknown):
            deliver_response("task-2", source, receiver=disconnect_after_two,
                             journal=self.journal, max_chunk_bytes=40)
        first_attempt = list(seen)
        resumed = []
        receipt = deliver_response(
            "task-2", source,
            receiver=lambda chunk: (resumed.append(chunk.index), self.ack(chunk))[1],
            journal=self.journal, max_chunk_bytes=40,
        )
        self.assertEqual(resumed, list(range(3, receipt.total_chunks + 1)))
        self.assertEqual(receipt.resumed_from_chunk, 2)
        self.assertEqual(first_attempt, [1, 2, 3])

    def test_mismatched_ack_blocks_without_advancing_cursor(self):
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


if __name__ == "__main__":
    unittest.main()
