import tempfile
import unittest
from pathlib import Path

from portable.provider_effect_reconciliation import ProviderEffectReconciliationAdapter
from portable.side_effect_ledger import DurableSideEffectLedger


class ProviderEffectReconciliationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "execution.sqlite3"
        self.key = "consequential-idempotency:release-52"
        self.payload = {"revision": "sha-52", "target": "staging"}
        self.ledger = DurableSideEffectLedger(self.path)
        self.ledger.begin(self.key, self.payload)
        self.ledger.require_reconciliation(self.key, "provider result was uncertain")
        self.calls = []

    def test_verified_receipt_is_saved_and_replayed(self):
        receipt = {"result": {"operation_id": "op-52", "status": "accepted"}}
        def lookup(key, digest):
            self.calls.append((key, digest))
            return {"evidence_ref": "provider-operation:op-52", "receipt": receipt}
        def verify(key, ref, candidate):
            return key == self.key and ref == "provider-operation:op-52" and candidate == receipt
        adapter = ProviderEffectReconciliationAdapter(
            self.path, provider_lookup=lookup, evidence_verifier=verify,
        )
        result = adapter.reconcile(self.key)
        self.assertEqual("reconciled", result.action)
        replay = DurableSideEffectLedger(self.path).begin(self.key, self.payload)
        self.assertEqual("replay", replay.action)
        self.assertEqual(receipt, replay.receipt)
        self.assertEqual(1, len(self.calls))

    def test_absent_operation_remains_blocked(self):
        adapter = ProviderEffectReconciliationAdapter(
            self.path, provider_lookup=lambda *_args: None,
            evidence_verifier=lambda *_args: True,
        )
        result = adapter.reconcile(self.key)
        self.assertEqual("blocked", result.action)
        self.assertEqual("reconcile_required", DurableSideEffectLedger(self.path).get(self.key)["state"])
        self.assertEqual("reconcile", DurableSideEffectLedger(self.path).begin(self.key, self.payload).action)

    def test_rejected_evidence_does_not_change_state(self):
        adapter = ProviderEffectReconciliationAdapter(
            self.path,
            provider_lookup=lambda *_args: {
                "evidence_ref": "provider-operation:op-52",
                "receipt": {"result": {"operation_id": "op-52", "status": "accepted"}},
            },
            evidence_verifier=lambda *_args: False,
        )
        result = adapter.reconcile(self.key)
        self.assertEqual("blocked", result.action)
        self.assertEqual("reconcile_required", DurableSideEffectLedger(self.path).get(self.key)["state"])

    def test_lookup_failure_remains_blocked(self):
        def lookup(*_args):
            raise TimeoutError("history unavailable")
        adapter = ProviderEffectReconciliationAdapter(
            self.path, provider_lookup=lookup, evidence_verifier=lambda *_args: True,
        )
        result = adapter.reconcile(self.key)
        self.assertEqual("blocked", result.action)
        self.assertEqual("reconcile_required", result.state)


if __name__ == "__main__":
    unittest.main()
