import tempfile
import unittest
from pathlib import Path

from portable.provider_operation_lifecycle import (
    ProviderOperationLifecycle, ProviderOutcomeBlocked, stable_provider_operation_id,
)
from portable.side_effect_ledger import DurableSideEffectLedger


class ProviderOperationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "side-effects.sqlite3"
        self.events = []

    def ledger(self, *, verify=None):
        return DurableSideEffectLedger(self.path, reconciliation_verifier=verify)

    def test_stable_operation_id_is_bound_to_key_provider_and_intent(self):
        intent = {"target": "staging", "revision": "abc"}
        first = stable_provider_operation_id("deploy-1", "provider-a", intent)
        self.assertEqual(first, stable_provider_operation_id("deploy-1", "provider-a", dict(reversed(list(intent.items())))))
        self.assertNotEqual(first, stable_provider_operation_id("deploy-2", "provider-a", intent))
        self.assertNotEqual(first, stable_provider_operation_id("deploy-1", "provider-b", intent))
        self.assertNotEqual(first, stable_provider_operation_id("deploy-1", "provider-a", {"revision": "def"}))

    def test_persists_before_dispatch_and_replays_durable_receipt(self):
        lifecycle = ProviderOperationLifecycle(self.ledger(), record_event=self.events.append)
        calls = []
        receipt = {"status": "completed", "provider_operation_id": "remote-42"}
        result = lifecycle.execute("deploy-1", "provider-a", {"target": "staging"},
                                  lambda intent, op_id, key: calls.append((intent, op_id, key)) or receipt)
        self.assertEqual(receipt, result)
        self.assertTrue(calls)
        self.assertEqual("completed", self.ledger().get("deploy-1")["state"])
        self.assertEqual("completed", lifecycle.execute(
            "deploy-1", "provider-a", {"target": "staging"},
            lambda *_: self.fail("completed operation must never be redispatched"))["status"])

    def test_lost_response_blocks_retry_until_provider_outcome_is_reconciled(self):
        verified = lambda key, evidence, receipt: evidence == "provider://op/42" and receipt.get("status") == "completed"
        lifecycle = ProviderOperationLifecycle(self.ledger(verify=verified), record_event=self.events.append)
        calls = []
        def lost_response(*args):
            calls.append(args)
            raise TimeoutError("response lost after provider may have accepted")
        with self.assertRaises(ProviderOutcomeBlocked):
            lifecycle.execute("deploy-2", "provider-a", {"target": "prod"}, lost_response)
        self.assertEqual(1, len(calls))
        self.assertEqual("reconcile_required", self.ledger().get("deploy-2")["state"])
        with self.assertRaises(ProviderOutcomeBlocked):
            lifecycle.execute("deploy-2", "provider-a", {"target": "prod"}, lost_response)
        self.assertEqual(1, len(calls))
        result = lifecycle.reconcile("deploy-2", "provider-a", {"target": "prod"},
            lambda op_id, key: {"status": "completed", "evidence_ref": "provider://op/42",
                                "receipt": {"status": "completed", "provider_operation_id": op_id}})
        self.assertEqual("completed", result["status"])
        self.assertEqual("completed", self.ledger().get("deploy-2")["state"])

    def test_missing_or_uncertain_provider_lookup_stays_blocked(self):
        lifecycle = ProviderOperationLifecycle(self.ledger())
        with self.assertRaises(ProviderOutcomeBlocked):
            lifecycle.execute("deploy-3", "provider-a", {"target": "prod"},
                              lambda *_: (_ for _ in ()).throw(ConnectionError("uncertain")))
        for lookup in (None, lambda *_: {"status": "not_found", "authoritative": True},
                       lambda *_: {"status": "pending"}, lambda *_: (_ for _ in ()).throw(OSError("lookup down"))):
            with self.assertRaises(ProviderOutcomeBlocked):
                lifecycle.reconcile("deploy-3", "provider-a", {"target": "prod"}, lookup)
        self.assertEqual("reconcile_required", self.ledger().get("deploy-3")["state"])

    def test_non_completed_dispatch_receipt_never_becomes_success(self):
        lifecycle = ProviderOperationLifecycle(self.ledger(), record_event=self.events.append)
        for index, receipt in enumerate((
            {"status": "pending"},
            {"status": "failed"},
            {"detail": "adapter returned without a status"},
            {"status": "unknown"},
        ), start=10):
            key = f"uncertain-{index}"
            calls = []
            with self.assertRaises(ProviderOutcomeBlocked):
                lifecycle.execute(
                    key, "provider-a", {"target": "prod", "case": index},
                    lambda *args, value=receipt: calls.append(args) or value,
                )
            self.assertEqual(1, len(calls))
            self.assertEqual("reconcile_required", self.ledger().get(key)["state"])
            with self.assertRaises(ProviderOutcomeBlocked):
                lifecycle.execute(
                    key, "provider-a", {"target": "prod", "case": index},
                    lambda *_: self.fail("uncertain operation must not be redispatched"),
                )
            self.assertEqual(1, len(calls))

    def test_explicit_legacy_success_is_accepted_but_conflicting_status_is_not(self):
        lifecycle = ProviderOperationLifecycle(self.ledger())
        receipt = {"success": True, "detail": "operation completed"}
        result = lifecycle.execute(
            "legacy-success", "provider-a", {"target": "staging"},
            lambda *_: receipt,
        )
        self.assertEqual(receipt, result)
        self.assertEqual("completed", self.ledger().get("legacy-success")["state"])

        with self.assertRaises(ProviderOutcomeBlocked):
            lifecycle.execute(
                "conflicting-status", "provider-a", {"target": "prod"},
                lambda *_: {"status": "pending", "success": True},
            )
        self.assertEqual("reconcile_required", self.ledger().get("conflicting-status")["state"])

    def test_reused_key_with_changed_intent_is_rejected(self):
        lifecycle = ProviderOperationLifecycle(self.ledger())
        lifecycle.execute("deploy-4", "provider-a", {"target": "staging"}, lambda *_: {"status": "completed"})
        with self.assertRaises(ValueError):
            lifecycle.execute("deploy-4", "provider-a", {"target": "production"}, lambda *_: {"status": "completed"})

    def test_journal_events_have_operation_identity_and_bounded_receipt_reference(self):
        lifecycle = ProviderOperationLifecycle(self.ledger(), record_event=self.events.append)
        lifecycle.execute("deploy-5", "provider-a", {"target": "staging"}, lambda *_: {"status": "completed"})
        self.assertEqual(["dispatching", "completed"], [event["state"] for event in self.events])
        self.assertTrue(all(event["operation_id"].startswith("auren-op-") for event in self.events))
        self.assertTrue(all(event["intent_digest"] for event in self.events))


if __name__ == "__main__":
    unittest.main()
