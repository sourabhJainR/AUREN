import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path

from portable.provider_operation_lifecycle import (
    ProviderOperationLifecycle, ProviderOutcomeBlocked, stable_provider_operation_id,
)
from portable.side_effect_ledger import DurableSideEffectLedger


_CRASH_OPERATION_KEY = "deploy:provider-crash:staging"
_CRASH_PROVIDER = "provider-a"
_CRASH_INTENT = {"target": "staging", "revision": "crash-revision"}


def _crash_inside_provider_dispatch(database: str, provider_log: str) -> None:
    lifecycle = ProviderOperationLifecycle(DurableSideEffectLedger(database))
    def dispatch(intent, operation_id, idempotency_key):
        with Path(provider_log).open("a", encoding="utf-8") as stream:
            stream.write(operation_id + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(82)
    lifecycle.execute(_CRASH_OPERATION_KEY, _CRASH_PROVIDER, _CRASH_INTENT, dispatch)
    os._exit(83)


def _crash_after_provider_receipt_before_event_projection(
    database: str, provider_log: str, event_log: str,
) -> None:
    def record_event(event):
        if event.get("state") == "completed":
            # The provider receipt is already committed; die before its progress
            # projection is written to the separate event sink.
            os._exit(84)
        with Path(event_log).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(event), sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    lifecycle = ProviderOperationLifecycle(
        DurableSideEffectLedger(database), record_event=record_event,
    )
    def dispatch(intent, operation_id, idempotency_key):
        with Path(provider_log).open("a", encoding="utf-8") as stream:
            stream.write(operation_id + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {"status": "completed", "provider_operation_id": operation_id}

    lifecycle.execute(
        "deploy:provider-receipt-crash:staging", "provider-a",
        {"target": "staging", "revision": "receipt-crash"}, dispatch,
    )
    os._exit(85)


class ProviderOperationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "side-effects.sqlite3"
        self.events = []

    def ledger(self, *, verify=None):
        return DurableSideEffectLedger(self.path, reconciliation_verifier=verify)


    def test_process_death_inside_real_dispatch_path_requires_lookup_before_replay(self):
        database = str(self.path)
        provider_log = str(self.path.parent / "provider-operations.log")
        process = multiprocessing.get_context("spawn").Process(
            target=_crash_inside_provider_dispatch,
            args=(database, provider_log),
        )
        process.start()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(3)
            self.fail("provider dispatch crash fixture exceeded its time budget")
        self.assertEqual(82, process.exitcode)
        self.assertEqual(1, len(Path(provider_log).read_text(encoding="utf-8").splitlines()))

        operation_id = stable_provider_operation_id(
            _CRASH_OPERATION_KEY, _CRASH_PROVIDER, _CRASH_INTENT,
        )
        verified = lambda key, evidence, receipt: (
            key == _CRASH_OPERATION_KEY
            and evidence == f"provider://operation/{operation_id}"
            and receipt.get("status") == "completed"
            and receipt.get("provider_operation_id") == operation_id
            and receipt.get("operation_id") == operation_id
            and receipt.get("idempotency_key") == _CRASH_OPERATION_KEY
            and receipt.get("provider") == _CRASH_PROVIDER
            and receipt.get("intent_digest") == self.ledger().get(_CRASH_OPERATION_KEY)["payload_digest"]
        )
        lifecycle = ProviderOperationLifecycle(self.ledger(verify=verified), record_event=self.events.append)
        with self.assertRaises(ProviderOutcomeBlocked):
            lifecycle.execute(
                _CRASH_OPERATION_KEY, _CRASH_PROVIDER, _CRASH_INTENT,
                lambda *_: self.fail("ambiguous provider operation must not be redispatched"),
            )
        self.assertEqual("reconcile_required", self.ledger().get(_CRASH_OPERATION_KEY)["state"])

        receipt = lifecycle.reconcile(
            _CRASH_OPERATION_KEY, _CRASH_PROVIDER, _CRASH_INTENT,
            lambda requested_id, key: {
                "status": "completed",
                "evidence_ref": f"provider://operation/{requested_id}",
                "receipt": {
                    "status": "completed",
                    "provider_operation_id": requested_id,
                    "operation_id": requested_id,
                    "idempotency_key": key,
                    "provider": _CRASH_PROVIDER,
                    "intent_digest": self.ledger().get(_CRASH_OPERATION_KEY)["payload_digest"],
                },
            },
        )
        self.assertEqual(operation_id, receipt["provider_operation_id"])
        self.assertEqual("completed", self.ledger().get(_CRASH_OPERATION_KEY)["state"])
        replay = lifecycle.execute(
            _CRASH_OPERATION_KEY, _CRASH_PROVIDER, _CRASH_INTENT,
            lambda *_: self.fail("reconciled operation must replay its receipt, not dispatch"),
        )
        self.assertEqual(receipt, replay)
        self.assertEqual([operation_id], Path(provider_log).read_text(encoding="utf-8").splitlines())


    def test_receipt_commit_before_event_projection_recovers_without_redispatch(self):
        operation_key = "deploy:provider-receipt-crash:staging"
        provider = "provider-a"
        intent = {"target": "staging", "revision": "receipt-crash"}
        database = str(self.path)
        provider_log = str(self.path.parent / "provider-operations.log")
        event_log = str(self.path.parent / "provider-events.jsonl")
        process = multiprocessing.get_context("spawn").Process(
            target=_crash_after_provider_receipt_before_event_projection,
            args=(database, provider_log, event_log),
        )
        process.start()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(3)
            self.fail("provider receipt/event crash fixture exceeded its time budget")
        self.assertEqual(84, process.exitcode)
        self.assertEqual(1, len(Path(provider_log).read_text(encoding="utf-8").splitlines()))
        initial_events = [
            json.loads(line) for line in Path(event_log).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(["dispatching"], [event["state"] for event in initial_events])

        operation_id = stable_provider_operation_id(operation_key, provider, intent)
        ledger = self.ledger()
        stored = ledger.get(operation_key)
        self.assertEqual("completed", stored["state"])
        self.assertEqual(operation_id, stored["receipt"]["provider_operation_id"])

        def record_event(event):
            with Path(event_log).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(dict(event), sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

        restarted = ProviderOperationLifecycle(ledger, record_event=record_event)
        receipt = restarted.execute(
            operation_key, provider, intent,
            lambda *_: self.fail("durable provider receipt must replay without dispatch"),
        )
        self.assertEqual(operation_id, receipt["provider_operation_id"])
        events = [json.loads(line) for line in Path(event_log).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(["dispatching", "completed"], [event["state"] for event in events])
        self.assertTrue(events[-1]["replay"])
        self.assertEqual([operation_id], Path(provider_log).read_text(encoding="utf-8").splitlines())

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

    def test_reconciliation_rejects_incomplete_receipt_even_if_lookup_says_completed(self):
        lifecycle = ProviderOperationLifecycle(self.ledger(verify=lambda *_: True))
        invalid_receipts = (
            {"status": "pending", "provider_operation_id": "remote-1"},
            {"status": "completed"},
            {"provider_operation_id": "remote-1"},
            {
                "status": "completed",
                "provider_operation_id": "remote-1",
                "operation_id": "auren-op-unrelated",
            },
        )
        for index, receipt in enumerate(invalid_receipts, start=20):
            key = f"reconcile-receipt-{index}"
            with self.assertRaises(ProviderOutcomeBlocked):
                lifecycle.execute(
                    key, "provider-a", {"target": "prod", "case": index},
                    lambda *_: (_ for _ in ()).throw(TimeoutError("response lost")),
                )
            with self.assertRaises(ProviderOutcomeBlocked):
                lifecycle.reconcile(
                    key, "provider-a", {"target": "prod", "case": index},
                    lambda op_id, _key, value=receipt: {
                        "status": "completed",
                        "evidence_ref": f"provider://operation/{index}",
                        "receipt": value,
                    },
                )
            self.assertEqual("reconcile_required", self.ledger().get(key)["state"])




    def test_reconciliation_requires_explicit_request_binding_fields(self):
        operation_key = "deploy:reconcile-missing-binding"
        provider = "provider-a"
        intent = {"target": "staging", "revision": "missing-binding"}
        operation_id = stable_provider_operation_id(operation_key, provider, intent)
        lifecycle = ProviderOperationLifecycle(self.ledger(verify=lambda *_: True), record_event=self.events.append)
        with self.assertRaises(ProviderOutcomeBlocked):
            lifecycle.execute(
                operation_key, provider, intent,
                lambda *_: (_ for _ in ()).throw(TimeoutError("response lost")),
            )
        digest = self.ledger().get(operation_key)["payload_digest"]
        incomplete_receipts = (
            {"status": "completed", "provider_operation_id": "remote-1"},
            {
                "status": "completed", "provider_operation_id": "remote-1",
                "operation_id": operation_id, "idempotency_key": operation_key,
                "provider": provider,
            },
            {
                "status": "completed", "provider_operation_id": "remote-1",
                "operation_id": operation_id, "idempotency_key": operation_key,
                "intent_digest": digest,
            },
        )
        for receipt in incomplete_receipts:
            with self.assertRaises(ProviderOutcomeBlocked):
                lifecycle.reconcile(
                    operation_key, provider, intent,
                    lambda _op_id, _key, value=receipt: {
                        "status": "completed", "evidence_ref": "provider://operations/remote-1",
                        "receipt": value,
                    },
                )
            self.assertEqual("reconcile_required", self.ledger().get(operation_key)["state"])

    def test_reconciliation_rejects_receipts_bound_to_other_provider_or_intent(self):
        operation_key = "deploy:reconcile-binding"
        provider = "provider-a"
        intent = {"target": "staging", "revision": "reconcile-binding"}
        operation_id = stable_provider_operation_id(operation_key, provider, intent)
        lifecycle = ProviderOperationLifecycle(self.ledger(verify=lambda *_: True), record_event=self.events.append)
        with self.assertRaises(ProviderOutcomeBlocked):
            lifecycle.execute(
                operation_key, provider, intent,
                lambda *_: (_ for _ in ()).throw(TimeoutError("response lost")),
            )

        bad_receipts = (
            {"provider": "provider-b"},
            {"intent_digest": "d" * 64},
        )
        for extra in bad_receipts:
            receipt = {
                "status": "completed",
                "provider_operation_id": "remote-reconcile-binding",
                "operation_id": operation_id,
                "idempotency_key": operation_key,
                **extra,
            }
            with self.assertRaisesRegex(ProviderOutcomeBlocked, "bound to the requested operation"):
                lifecycle.reconcile(
                    operation_key, provider, intent,
                    lambda _op_id, _key, value=receipt: {
                        "status": "completed",
                        "evidence_ref": "provider://operations/remote-reconcile-binding",
                        "receipt": value,
                    },
                )
            self.assertEqual("reconcile_required", self.ledger().get(operation_key)["state"])

    def test_reconciliation_accepts_distinct_provider_remote_id_when_trusted_binding_matches(self):
        operation_key = "deploy:remote-id-differs"
        provider = "provider-a"
        intent = {"target": "staging", "revision": "remote-id"}
        operation_id = stable_provider_operation_id(operation_key, provider, intent)
        remote_id = "remote-provider-9081"
        verifier_calls = []

        def verify(key, evidence_ref, receipt):
            verifier_calls.append((key, evidence_ref, dict(receipt)))
            return (
                key == operation_key
                and evidence_ref == f"provider://operations/{remote_id}"
                and receipt.get("provider_operation_id") == remote_id
                and receipt.get("operation_id") == operation_id
                and receipt.get("idempotency_key") == operation_key
                and receipt.get("provider") == provider
                and receipt.get("intent_digest") == self.ledger().get(operation_key)["payload_digest"]
            )

        lifecycle = ProviderOperationLifecycle(self.ledger(verify=verify), record_event=self.events.append)
        with self.assertRaises(ProviderOutcomeBlocked):
            lifecycle.execute(
                operation_key, provider, intent,
                lambda *_: (_ for _ in ()).throw(TimeoutError("response lost after provider acceptance")),
            )

        receipt = lifecycle.reconcile(
            operation_key, provider, intent,
            lambda requested_id, requested_key: {
                "status": "completed",
                "evidence_ref": f"provider://operations/{remote_id}",
                "receipt": {
                    "status": "completed",
                    "provider_operation_id": remote_id,
                    "operation_id": requested_id,
                    "idempotency_key": requested_key,
                    "provider": provider,
                    "intent_digest": self.ledger().get(operation_key)["payload_digest"],
                },
            },
        )
        self.assertEqual(remote_id, receipt["provider_operation_id"])
        self.assertEqual(operation_id, receipt["operation_id"])
        self.assertEqual(1, len(verifier_calls))
        self.assertEqual("completed", self.ledger().get(operation_key)["state"])

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

    def test_completed_receipt_with_mismatched_provider_operation_id_is_blocked(self):
        lifecycle = ProviderOperationLifecycle(self.ledger(), record_event=self.events.append)
        intent = {"target": "production", "revision": "release-42"}

        with self.assertRaisesRegex(ProviderOutcomeBlocked, "different operation"):
            lifecycle.execute(
                "deploy:binding-check", "provider-a", intent,
                lambda *_: {
                    "status": "completed",
                    "operation_id": "provider-operation-from-another-request",
                },
            )

        self.assertEqual(
            "reconcile_required",
            self.ledger().get("deploy:binding-check")["state"],
        )
        self.assertTrue(any(
            event.get("reason") == "provider_operation_binding_mismatch"
            for event in self.events
        ))
        with self.assertRaises(ProviderOutcomeBlocked):
            lifecycle.execute(
                "deploy:binding-check", "provider-a", intent,
                lambda *_: self.fail("mismatched receipt must never permit redispatch"),
            )
        self.assertNotEqual("completed", self.ledger().get("deploy:binding-check")["state"])


    def test_dispatch_receipt_provider_and_intent_bindings_must_match(self):
        cases = (
            ("wrong-provider", {"provider": "provider-b"}),
            ("wrong-intent", {"intent_digest": "c" * 64}),
        )
        for key, extra in cases:
            lifecycle = ProviderOperationLifecycle(self.ledger(), record_event=self.events.append)
            receipt = {
                "status": "completed",
                "provider_operation_id": f"remote-{key}",
                **extra,
            }
            with self.assertRaisesRegex(ProviderOutcomeBlocked, "different operation"):
                lifecycle.execute(
                    key, "provider-a", {"target": "staging", "operation": key},
                    lambda *_args, value=receipt: value,
                )
            self.assertEqual("reconcile_required", self.ledger().get(key)["state"])
            self.assertTrue(any(
                event.get("reason") == "provider_operation_binding_mismatch"
                and event.get("binding_field") in {"provider", "intent_digest"}
                for event in self.events
            ))

    def test_explicit_legacy_success_is_accepted_but_conflicting_status_is_not(self):
        lifecycle = ProviderOperationLifecycle(self.ledger())
        receipt = {"success": True, "detail": "operation completed", "provider_operation_id": "legacy-remote-1"}
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

        nested = {"result": {"success": True, "detail": "completed"}, "provider_operation_id": "remote-1"}
        self.assertEqual(nested, lifecycle.execute(
            "nested-success", "provider-a", {"target": "staging"},
            lambda *_: nested,
        ))
        with self.assertRaises(ProviderOutcomeBlocked):
            lifecycle.execute(
                "nested-pending", "provider-a", {"target": "prod"},
                lambda *_: {"result": {"status": "pending", "success": True}},
            )


    def test_completed_dispatch_without_provider_operation_id_stays_reconciliation_required(self):
        for index, receipt in enumerate(
            ({"status": "completed"}, {"success": True, "detail": "accepted"}),
            start=60,
        ):
            key = f"missing-remote-operation-id-{index}"
            lifecycle = ProviderOperationLifecycle(self.ledger(), record_event=self.events.append)
            with self.assertRaisesRegex(ProviderOutcomeBlocked, "operation identifier"):
                lifecycle.execute(
                    key, "provider-a", {"target": "staging", "case": index},
                    lambda *_args, value=receipt: value,
                )
            self.assertEqual("reconcile_required", self.ledger().get(key)["state"])
            with self.assertRaises(ProviderOutcomeBlocked):
                lifecycle.execute(
                    key, "provider-a", {"target": "staging", "case": index},
                    lambda *_: self.fail("receipt without remote operation ID must not redispatch"),
                )
            self.assertEqual("reconcile_required", self.ledger().get(key)["state"])

    def test_reused_key_with_changed_intent_is_rejected(self):
        lifecycle = ProviderOperationLifecycle(self.ledger())
        lifecycle.execute("deploy-4", "provider-a", {"target": "staging"}, lambda *_: {"status": "completed", "provider_operation_id": "remote-4"})
        with self.assertRaises(ValueError):
            lifecycle.execute("deploy-4", "provider-a", {"target": "production"}, lambda *_: {"status": "completed"})

    def test_journal_events_have_operation_identity_and_bounded_receipt_reference(self):
        lifecycle = ProviderOperationLifecycle(self.ledger(), record_event=self.events.append)
        lifecycle.execute("deploy-5", "provider-a", {"target": "staging"}, lambda *_: {"status": "completed", "provider_operation_id": "remote-5"})
        self.assertEqual(["dispatching", "completed"], [event["state"] for event in self.events])
        self.assertTrue(all(event["operation_id"].startswith("auren-op-") for event in self.events))
        self.assertTrue(all(event["intent_digest"] for event in self.events))


if __name__ == "__main__":
    unittest.main()
