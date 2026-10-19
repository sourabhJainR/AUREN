import tempfile
import unittest
from pathlib import Path

from portable.side_effect_ledger import (
    DurableSideEffectLedger,
    IdempotencyConflict,
    InvalidSideEffectTransition,
)


class DurableSideEffectLedgerTests(unittest.TestCase):
    def test_completed_effect_replays_receipt_without_reexecution(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "effects.sqlite3"
            ledger = DurableSideEffectLedger(path)
            first = ledger.begin("deploy:run-1", {"revision": "abc", "target": "staging"})
            self.assertEqual("execute", first.action)
            receipt = {"provider_operation_id": "op-123", "status": "accepted"}
            ledger.complete(first.idempotency_key, receipt)

            restarted = DurableSideEffectLedger(path)
            replay = restarted.begin("deploy:run-1", {"target": "staging", "revision": "abc"})
            self.assertEqual("replay", replay.action)
            self.assertEqual(receipt, replay.receipt)
            self.assertEqual("completed", replay.state)

    def test_same_key_with_different_payload_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DurableSideEffectLedger(Path(tmp) / "effects.sqlite3")
            ledger.begin("effect-1", {"amount": 5})
            with self.assertRaises(IdempotencyConflict):
                ledger.begin("effect-1", {"amount": 6})

    def test_interrupted_execution_requires_reconciliation_not_automatic_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "effects.sqlite3"
            ledger = DurableSideEffectLedger(path)
            ledger.begin("effect-1", {"resource": "production"})
            restarted = DurableSideEffectLedger(path)
            decision = restarted.begin("effect-1", {"resource": "production"})
            self.assertEqual("reconcile", decision.action)
            self.assertEqual("reconcile_required", decision.state)
            self.assertEqual("reconcile_required", restarted.get("effect-1")["state"])

    def test_retry_requires_explicit_operator_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DurableSideEffectLedger(Path(tmp) / "effects.sqlite3")
            ledger.begin("effect-1", {"resource": "staging"})
            ledger.require_reconciliation("effect-1", "provider response was lost")
            with self.assertRaises(ValueError):
                ledger.authorize_retry(
                    "effect-1", operator_reason="checked logs", confirmed_no_effect=False
                )
            ledger.authorize_retry(
                "effect-1", operator_reason="provider confirms no operation exists",
                confirmed_no_effect=True,
            )
            self.assertEqual("execute", ledger.begin("effect-1", {"resource": "staging"}).action)

    def test_completed_receipt_cannot_be_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DurableSideEffectLedger(Path(tmp) / "effects.sqlite3")
            ledger.begin("effect-1", {"action": "publish"})
            ledger.complete("effect-1", {"receipt": "immutable"})
            with self.assertRaises(IdempotencyConflict):
                ledger.complete("effect-1", {"receipt": "changed"})

    def test_invalid_transitions_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DurableSideEffectLedger(Path(tmp) / "effects.sqlite3")
            with self.assertRaises(InvalidSideEffectTransition):
                ledger.complete("missing", {"ok": True})
            ledger.begin("effect-1", {"action": "publish"})
            ledger.require_reconciliation("effect-1", "uncertain")
            with self.assertRaises(InvalidSideEffectTransition):
                ledger.complete("effect-1", {"ok": True})


if __name__ == "__main__":
    unittest.main()
