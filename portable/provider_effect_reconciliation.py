"""Host adapter for reconciling ambiguous provider effects through the canonical ledger.

This module does not schedule work or create another store. The host must supply a
trusted provider-history lookup and a separate evidence verifier.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .side_effect_ledger import (
    DurableSideEffectLedger,
    IdempotencyConflict,
    InvalidSideEffectTransition,
)


@dataclass(frozen=True, slots=True)
class ProviderReconciliationOutcome:  # pragma: no cover - passive result DTO
    action: str
    ledger_key: str
    state: str
    reason: str
    evidence_ref: str | None = None
    receipt: Mapping[str, Any] | None = None


class ProviderEffectReconciliationAdapter:
    """Resolve ambiguous provider effects without automatically retrying them.

    provider_lookup must query a trusted provider API using the durable ledger key
    and payload digest. It returns a mapping with an evidence reference and a
    controller-compatible receipt, or None when no operation is found.
    evidence_verifier must independently validate that evidence before the
    ledger can accept the receipt.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        provider_lookup: Callable[[str, str], Mapping[str, Any] | None],
        evidence_verifier: Callable[[str, str, Mapping[str, Any]], bool],
    ) -> None:
        if not callable(provider_lookup):
            raise ValueError("trusted provider_lookup callback is required")
        if not callable(evidence_verifier):
            raise ValueError("trusted evidence_verifier callback is required")
        self._provider_lookup = provider_lookup
        self._ledger = DurableSideEffectLedger(
            database_path,
            reconciliation_verifier=evidence_verifier,
        )

    def reconcile(self, ledger_key: str) -> ProviderReconciliationOutcome:
        """Look up an ambiguous operation and record only a verified provider result."""
        if not isinstance(ledger_key, str) or not ledger_key.strip():
            raise ValueError("ledger_key is required")
        key = ledger_key.strip()
        record = self._ledger.get(key)
        if record is None:
            raise InvalidSideEffectTransition("unknown side effect; refusing provider lookup")

        if record["state"] == "completed":
            return ProviderReconciliationOutcome(
                "replay", key, "completed", "immutable receipt already exists",
                receipt=record.get("receipt"),
            )
        if record["state"] not in {"executing", "reconcile_required"}:
            return ProviderReconciliationOutcome(
                "blocked", key, str(record["state"]),
                "side effect is not in an ambiguous state; no provider action was taken",
            )

        if record["state"] == "executing":
            self._ledger.require_reconciliation(
                key, "provider operation lookup required after interrupted dispatch",
            )
            record = self._ledger.get(key) or record

        try:
            provider_result = self._provider_lookup(key, str(record["payload_digest"]))
        except Exception as exc:
            self._keep_reconciliation_required(key, f"provider lookup failed: {type(exc).__name__}")
            latest = self._ledger.get(key) or record
            return ProviderReconciliationOutcome(
                "blocked", key, str(latest["state"]),
                f"trusted provider lookup failed ({type(exc).__name__}); automatic retry is prohibited",
            )

        if provider_result is None:
            return ProviderReconciliationOutcome(
                "blocked", key, "reconcile_required",
                "provider operation not found; absence does not prove no effect; operator evidence is required",
            )
        if not isinstance(provider_result, Mapping):
            return ProviderReconciliationOutcome(
                "blocked", key, "reconcile_required",
                "provider lookup returned an invalid result; reconciliation remains required",
            )

        evidence_ref = provider_result.get("evidence_ref")
        receipt = provider_result.get("receipt")
        if (
            not isinstance(evidence_ref, str) or not evidence_ref.strip()
            or not isinstance(receipt, Mapping)
            or not isinstance(receipt.get("result"), Mapping)
        ):
            return ProviderReconciliationOutcome(
                "blocked", key, "reconcile_required",
                "provider result lacks an evidence reference or controller-compatible receipt",
            )

        try:
            decision = self._ledger.record_reconciled_effect(
                key, evidence_ref=evidence_ref, receipt=receipt,
            )
        except (PermissionError, InvalidSideEffectTransition, IdempotencyConflict) as exc:
            latest = self._ledger.get(key)
            if latest is not None and latest["state"] == "completed":
                return ProviderReconciliationOutcome(
                    "replay", key, "completed", "receipt was committed by another reconciler",
                    receipt=latest.get("receipt"),
                )
            state = str(latest["state"]) if latest is not None else "unknown"
            return ProviderReconciliationOutcome(
                "blocked", key, state,
                f"provider evidence was not accepted ({type(exc).__name__}); no redispatch occurred",
            )

        return ProviderReconciliationOutcome(
            decision.action, key, decision.state, "provider evidence verified",
            evidence_ref=evidence_ref.strip(), receipt=dict(receipt),
        )

    def _keep_reconciliation_required(self, ledger_key: str, reason: str) -> None:
        try:
            self._ledger.require_reconciliation(ledger_key, reason)
        except InvalidSideEffectTransition:
            return
