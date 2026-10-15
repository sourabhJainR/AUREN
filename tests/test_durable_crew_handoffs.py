from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from portable.durable_crew_handoffs import DurableHandoffLedger


class DurableHandoffLedgerTests(unittest.TestCase):
    def test_enqueue_is_idempotent_and_conflicts_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DurableHandoffLedger(Path(tmp) / "queue.json")
            first = ledger.enqueue(handoff_id="h1", source_crew="api", target_crew="qa", task="review", payload={"change": "x"})
            second = ledger.enqueue(handoff_id="h1", source_crew="api", target_crew="qa", task="review", payload={"change": "x"})
            self.assertEqual(first, second)
            with self.assertRaisesRegex(ValueError, "conflicting"):
                ledger.enqueue(handoff_id="h1", source_crew="api", target_crew="qa", task="review", payload={"change": "y"})

    def test_restart_resumes_inflight_as_claimable_and_tracks_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue.json"
            ledger = DurableHandoffLedger(path)
            ledger.enqueue(handoff_id="h1", source_crew="api", target_crew="qa", task="review", payload={"change": "x"})
            self.assertEqual(ledger.claim("h1")["attempts"], 1)
            resumed = DurableHandoffLedger(path)
            self.assertEqual(resumed.claim("h1")["attempts"], 2)

    def test_completion_requires_independent_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DurableHandoffLedger(Path(tmp) / "queue.json")
            ledger.enqueue(handoff_id="h1", source_crew="api", target_crew="qa", task="review", payload={})
            ledger.claim("h1")
            with self.assertRaises(PermissionError):
                ledger.complete("h1", {"independent": False, "reviewer_id": "author"})
            result = ledger.complete("h1", {"independent": True, "reviewer_id": "qa-independent", "receipt_digest": "abc"})
            self.assertEqual(result["state"], "completed")
            with self.assertRaises(ValueError):
                ledger.claim("h1")

    def test_checkpoint_tampering_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue.json"
            ledger = DurableHandoffLedger(path)
            ledger.enqueue(handoff_id="h1", source_crew="api", target_crew="qa", task="review", payload={})
            data = json.loads(path.read_text())
            data["records"][0]["task"] = "tampered"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                DurableHandoffLedger(path)

    def test_failed_atomic_replace_preserves_last_durable_checkpoint(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue.json"
            ledger = DurableHandoffLedger(path)
            ledger.enqueue(
                handoff_id="durable-1", source_crew="api", target_crew="qa",
                task="review", payload={"change": "known-good"},
            )
            durable_bytes = path.read_bytes()

            # Simulate a crash/failure at the atomic publication boundary.
            with patch("portable.durable_crew_handoffs.os.replace", side_effect=OSError("injected replace failure")):
                with self.assertRaisesRegex(OSError, "injected replace failure"):
                    ledger.enqueue(
                        handoff_id="not-durable", source_crew="api", target_crew="qa",
                        task="review", payload={"change": "must-not-persist"},
                    )

            self.assertEqual(path.read_bytes(), durable_bytes)
            self.assertEqual(list(Path(tmp).glob(".queue.json.*.tmp")), [])
            resumed = DurableHandoffLedger(path)
            self.assertEqual(resumed.get("durable-1")["state"], "queued")
            self.assertIsNone(resumed.get("not-durable"))

    def test_cross_crew_and_capacity_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DurableHandoffLedger(Path(tmp) / "queue.json", max_records=1)
            with self.assertRaisesRegex(ValueError, "distinct"):
                ledger.enqueue(handoff_id="bad", source_crew="api", target_crew="api", task="x", payload={})
            ledger.enqueue(handoff_id="h1", source_crew="api", target_crew="qa", task="review", payload={})
            with self.assertRaisesRegex(RuntimeError, "capacity"):
                ledger.enqueue(handoff_id="h2", source_crew="api", target_crew="qa", task="review", payload={})


if __name__ == "__main__":
    unittest.main()


    def test_claim_attempt_budget_is_durable_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue.json"
            ledger = DurableHandoffLedger(path, max_attempts=2)
            ledger.enqueue(handoff_id="bounded", source_crew="api", target_crew="qa", task="review", payload={})
            self.assertEqual(ledger.claim("bounded")["attempts"], 1)
            # An interrupted in-flight delivery can be claimed once more.
            restarted = DurableHandoffLedger(path, max_attempts=2)
            self.assertEqual(restarted.claim("bounded")["attempts"], 2)

            # The next restart cannot retry forever: exhaustion is persisted.
            recovered = DurableHandoffLedger(path, max_attempts=2)
            with self.assertRaisesRegex(RuntimeError, "attempt budget exhausted"):
                recovered.claim("bounded")
            final = DurableHandoffLedger(path, max_attempts=2).get("bounded")
            self.assertEqual(final["state"], "rejected")
            self.assertEqual(final["attempts"], 2)
            self.assertEqual(final["review_receipt"]["reason"], "handoff attempt budget exhausted")

    def test_claim_attempt_budget_must_be_bounded_positive_integer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue.json"
            for value in (0, -1, 1.5, True, 101):
                with self.subTest(value=value):
                    with self.assertRaisesRegex(ValueError, "max_attempts"):
                        DurableHandoffLedger(path, max_attempts=value)
