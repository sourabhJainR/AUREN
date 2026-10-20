from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.side_effect_ledger import DurableSideEffectLedger, IdempotencyConflict, InvalidSideEffectTransition


class ProviderEffectReconciliationTests(unittest.TestCase):
    def test_reconcile_provider_success_after_receipt_write_failure_without_reexecution(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "effects.sqlite3"
            calls = []
            ledger = DurableSideEffectLedger(path)
            decision = ledger.begin("deploy:run-42", {"revision": "sha-abc", "target": "staging"})
            self.assertEqual("execute", decision.action)
            calls.append("provider-deploy")
            provider_receipt = {"provider_operation_id": "op-42", "status": "accepted"}
            ledger.require_reconciliation(decision.idempotency_key, "receipt persistence failed after provider success")

            restarted = DurableSideEffectLedger(
                path,
                reconciliation_verifier=lambda key, ref, receipt: (
                    key == "deploy:run-42" and ref == "provider://operations/op-42"
                    and receipt.get("provider_operation_id") == "op-42"
                ),
            )
            reconciled = restarted.record_reconciled_effect(
                decision.idempotency_key,
                evidence_ref="provider://operations/op-42",
                receipt=provider_receipt,
            )
            self.assertEqual("reconciled", reconciled.action)
            replay = restarted.begin(decision.idempotency_key, {"target": "staging", "revision": "sha-abc"})
            self.assertEqual("replay", replay.action)
            self.assertEqual(provider_receipt, replay.receipt)
            self.assertEqual(["provider-deploy"], calls)

    def test_changed_payload_cannot_reuse_reconciled_idempotency_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "effects.sqlite3"
            ledger = DurableSideEffectLedger(path, reconciliation_verifier=lambda *args: True)
            ledger.begin("deploy:run-43", {"revision": "sha-abc"})
            ledger.require_reconciliation("deploy:run-43", "provider result ambiguous")
            ledger.record_reconciled_effect(
                "deploy:run-43", evidence_ref="provider://operations/op-43",
                receipt={"provider_operation_id": "op-43"},
            )
            with self.assertRaises(IdempotencyConflict):
                ledger.begin("deploy:run-43", {"revision": "sha-different"})


if __name__ == "__main__":
    unittest.main()
