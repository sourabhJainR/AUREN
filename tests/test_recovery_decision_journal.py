from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.recovery_decision_journal import RecoveryDecisionJournal


class RecoveryDecisionJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "recovery.sqlite3"
        self.journal = RecoveryDecisionJournal(self.path)

    def record(self, **overrides):
        values = {
            "work_id": "work-123",
            "phase": "execution",
            "attempt_number": 1,
            "action": "alternate",
            "strategy_id": "patch_then_test",
            "failure_class": "test_failure",
            "attempts_remaining": 2,
            "checkpoint_digest": "a" * 64,
            "side_effect_state": "none",
            "reason_code": "no_progress",
        }
        values.update(overrides)
        return self.journal.record(**values)

    def test_receipt_is_durable_and_readable_after_restart(self):
        receipt = self.record()
        restored = RecoveryDecisionJournal(self.path).get("work-123", "execution", 1)
        self.assertEqual(receipt, restored)
        self.assertEqual(receipt.decision_digest, restored.decision_digest)

    def test_identical_write_is_idempotent_but_conflict_fails_closed(self):
        first = self.record()
        second = self.record()
        self.assertEqual(first, second)
        with self.assertRaisesRegex(ValueError, "different receipt"):
            self.record(strategy_id="different_strategy")

    def test_decisions_are_ordered_and_queryable_by_work(self):
        self.record()
        self.record(attempt_number=2, action="block", strategy_id=None,
                    attempts_remaining=0, reason_code="budget_exhausted")
        receipts = self.journal.list_for_work("work-123", "execution")
        self.assertEqual([1, 2], [item.attempt_number for item in receipts])
        self.assertEqual("block", receipts[1].action)

    def test_rejects_invalid_digest_and_unsafe_action_shape(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.record(checkpoint_digest="not-a-digest")
        with self.assertRaisesRegex(ValueError, "require a strategy"):
            self.record(action="alternate", strategy_id=None)
        with self.assertRaisesRegex(ValueError, "cannot select a strategy"):
            self.record(action="block", strategy_id="patch_then_test")

    def test_untrusted_free_form_text_is_not_accepted_as_receipt_metadata(self):
        with self.assertRaisesRegex(ValueError, "identifier token"):
            self.record(reason_code="provider response included secret\n")

    def test_unknown_decision_returns_none(self):
        self.assertIsNone(self.journal.get("work-123", "execution", 1))


if __name__ == "__main__":
    unittest.main()
