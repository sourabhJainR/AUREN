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

    def test_reconciliation_verifier_exception_fails_closed_without_state_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "effects.sqlite3"
            ledger = DurableSideEffectLedger(path)
            ledger.begin("deploy:run-44", {"revision": "sha-abc"})
            ledger.require_reconciliation("deploy:run-44", "receipt persistence interrupted")

            def broken_verifier(*_args):
                raise OSError("provider history unavailable")

            restarted = DurableSideEffectLedger(path, reconciliation_verifier=broken_verifier)
            with self.assertRaisesRegex(PermissionError, "failed closed"):
                restarted.record_reconciled_effect(
                    "deploy:run-44", evidence_ref="provider://operations/op-44",
                    receipt={"provider_operation_id": "op-44"},
                )
            self.assertEqual("reconcile_required", restarted.get("deploy:run-44")["state"])

    def test_reconciliation_requires_prior_ambiguous_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DurableSideEffectLedger(
                Path(tmp) / "effects.sqlite3", reconciliation_verifier=lambda *_args: True,
            )
            ledger.begin("deploy:run-45", {"revision": "sha-abc"})
            with self.assertRaises(InvalidSideEffectTransition):
                ledger.record_reconciled_effect(
                    "deploy:run-45", evidence_ref="provider://operations/op-45",
                    receipt={"provider_operation_id": "op-45"},
                )
            self.assertEqual("executing", ledger.get("deploy:run-45")["state"])

    def test_concurrent_reconciliation_cannot_overwrite_receipt_or_reexecute(self):
        from concurrent.futures import ThreadPoolExecutor

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "effects.sqlite3"
            ledger = DurableSideEffectLedger(path, reconciliation_verifier=lambda *_args: True)
            ledger.begin("deploy:run-46", {"revision": "sha-abc"})
            ledger.require_reconciliation("deploy:run-46", "receipt persistence interrupted")
            def reconcile():
                try:
                    return ledger.record_reconciled_effect(
                        "deploy:run-46", evidence_ref="provider://operations/op-46",
                        receipt={"provider_operation_id": "op-46"},
                    )
                except InvalidSideEffectTransition:
                    # One writer wins; the loser must fail closed rather than overwrite.
                    return "lost_race"
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _n: reconcile(), range(2)))
            self.assertEqual(1, sum(getattr(result, "action", None) == "reconciled" for result in results))
            self.assertEqual(1, sum(result == "lost_race" for result in results))
            self.assertEqual("completed", ledger.get("deploy:run-46")["state"])
            replay = ledger.begin("deploy:run-46", {"revision": "sha-abc"})
            self.assertEqual("replay", replay.action)
            self.assertEqual({"provider_operation_id": "op-46"}, replay.receipt)


if __name__ == "__main__":
    unittest.main()
