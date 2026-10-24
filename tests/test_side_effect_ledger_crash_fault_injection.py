from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest

from portable.side_effect_ledger import DurableSideEffectLedger


_IDEMPOTENCY_KEY = "deploy:crash-matrix:staging"
_PAYLOAD = {"revision": "release-sha", "target": "staging"}


def _crash_after_claim(database: str) -> None:
    ledger = DurableSideEffectLedger(database)
    decision = ledger.begin(_IDEMPOTENCY_KEY, _PAYLOAD)
    if decision.action != "execute":
        os._exit(91)
    os._exit(71)


def _crash_after_provider_accept(database: str, provider_log: str) -> None:
    ledger = DurableSideEffectLedger(database)
    decision = ledger.begin(_IDEMPOTENCY_KEY, _PAYLOAD)
    if decision.action != "execute":
        os._exit(92)
    # Model a remote provider committing an operation before AUREN can persist
    # its receipt. os._exit simulates process death without finally/atexit work.
    with Path(provider_log).open("a", encoding="utf-8") as stream:
        stream.write("provider-op-001\n")
        stream.flush()
        os.fsync(stream.fileno())
    os._exit(72)


def _crash_process(target, *args) -> int:
    process = multiprocessing.get_context("spawn").Process(target=target, args=args)
    process.start()
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(3)
        raise AssertionError("crash-injection child exceeded its time budget")
    return int(process.exitcode)


class SideEffectProcessCrashFaultInjectionTests(unittest.TestCase):
    def test_crash_after_claim_requires_explicit_no_effect_retry_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = str(Path(tmp) / "effects.sqlite3")
            self.assertEqual(71, _crash_process(_crash_after_claim, database))

            ledger = DurableSideEffectLedger(
                database,
                retry_authorizer=lambda key, operator, evidence, reason: (
                    key == _IDEMPOTENCY_KEY
                    and operator == "operator-test"
                    and evidence == "provider-query:no-effect"
                    and reason == "provider confirms no operation was created"
                ),
            )
            recovered = ledger.begin(_IDEMPOTENCY_KEY, _PAYLOAD)
            self.assertEqual("reconcile", recovered.action)
            self.assertEqual("reconcile_required", recovered.state)

            with self.assertRaises(PermissionError):
                ledger.authorize_retry(
                    _IDEMPOTENCY_KEY,
                    operator_reason="retry without trusted evidence",
                    operator_id="operator-test",
                    evidence_ref="untrusted",
                    confirmed_no_effect=True,
                )
            ledger.authorize_retry(
                _IDEMPOTENCY_KEY,
                operator_reason="provider confirms no operation was created",
                operator_id="operator-test",
                evidence_ref="provider-query:no-effect",
                confirmed_no_effect=True,
            )
            retry = ledger.begin(_IDEMPOTENCY_KEY, _PAYLOAD)
            self.assertEqual("execute", retry.action)
            self.assertEqual("executing", retry.state)

    def test_crash_after_provider_accept_reconciles_without_redispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = str(Path(tmp) / "effects.sqlite3")
            provider_log = str(Path(tmp) / "provider-operations.log")
            self.assertEqual(72, _crash_process(_crash_after_provider_accept, database, provider_log))
            self.assertEqual(["provider-op-001"], Path(provider_log).read_text(encoding="utf-8").splitlines())

            def trusted_reconciliation(key, evidence_ref, receipt):
                return (
                    key == _IDEMPOTENCY_KEY
                    and evidence_ref == "provider-operation:provider-op-001"
                    and receipt.get("provider_operation_id") == "provider-op-001"
                    and Path(provider_log).read_text(encoding="utf-8").splitlines() == ["provider-op-001"]
                )

            ledger = DurableSideEffectLedger(database, reconciliation_verifier=trusted_reconciliation)
            recovered = ledger.begin(_IDEMPOTENCY_KEY, _PAYLOAD)
            self.assertEqual("reconcile", recovered.action)
            self.assertEqual("reconcile_required", recovered.state)

            reconciled = ledger.record_reconciled_effect(
                _IDEMPOTENCY_KEY,
                evidence_ref="provider-operation:provider-op-001",
                receipt={"provider_operation_id": "provider-op-001", "status": "accepted"},
            )
            self.assertEqual("reconciled", reconciled.action)
            replay = ledger.begin(_IDEMPOTENCY_KEY, _PAYLOAD)
            self.assertEqual("replay", replay.action)
            self.assertEqual("completed", replay.state)
            self.assertEqual("provider-op-001", replay.receipt["provider_operation_id"])
            self.assertEqual(["provider-op-001"], Path(provider_log).read_text(encoding="utf-8").splitlines())


if __name__ == "__main__":
    unittest.main()


_COMPLETED_KEY = "deploy:crash-matrix:completed-before-receipt"
_COMPLETED_PAYLOAD = {"revision": "release-sha", "target": "staging"}


def _crash_after_provider_completion(database: str, provider_log: str) -> None:
    ledger = DurableSideEffectLedger(database)
    decision = ledger.begin(_COMPLETED_KEY, _COMPLETED_PAYLOAD)
    if decision.action != "execute":
        os._exit(93)
    # Model a provider completing the irreversible operation while AUREN dies
    # before the local completion receipt can commit.
    with Path(provider_log).open("a", encoding="utf-8") as stream:
        stream.write("provider-op-002:completed\n")
        stream.flush()
        os.fsync(stream.fileno())
    os._exit(73)


class ProviderCompletionReceiptCrashTests(unittest.TestCase):
    def test_completed_remote_effect_is_reconciled_without_redispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = str(Path(tmp) / "effects.sqlite3")
            provider_log = str(Path(tmp) / "provider-operations.log")
            self.assertEqual(73, _crash_process(_crash_after_provider_completion, database, provider_log))
            self.assertEqual(
                ["provider-op-002:completed"],
                Path(provider_log).read_text(encoding="utf-8").splitlines(),
            )

            def trusted_reconciliation(key, evidence_ref, receipt):
                return (
                    key == _COMPLETED_KEY
                    and evidence_ref == "provider-operation:provider-op-002"
                    and receipt.get("provider_operation_id") == "provider-op-002"
                    and receipt.get("status") == "completed"
                    and Path(provider_log).read_text(encoding="utf-8").splitlines()
                    == ["provider-op-002:completed"]
                )

            ledger = DurableSideEffectLedger(database, reconciliation_verifier=trusted_reconciliation)
            recovered = ledger.begin(_COMPLETED_KEY, _COMPLETED_PAYLOAD)
            self.assertEqual("reconcile", recovered.action)
            self.assertEqual("reconcile_required", recovered.state)

            reconciled = ledger.record_reconciled_effect(
                _COMPLETED_KEY,
                evidence_ref="provider-operation:provider-op-002",
                receipt={"provider_operation_id": "provider-op-002", "status": "completed"},
            )
            self.assertEqual("reconciled", reconciled.action)
            replay = ledger.begin(_COMPLETED_KEY, _COMPLETED_PAYLOAD)
            self.assertEqual("replay", replay.action)
            self.assertEqual("completed", replay.state)
            self.assertEqual(
                ["provider-op-002:completed"],
                Path(provider_log).read_text(encoding="utf-8").splitlines(),
                "recovery must not redispatch a remotely completed operation",
            )
