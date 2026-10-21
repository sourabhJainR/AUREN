"""Provider write lifecycle backed by the canonical DurableSideEffectLedger.

Composes with the supervisor's existing ledger and callbacks; creates no second
operation database, polling daemon, or retry loop.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping

from .side_effect_ledger import DurableSideEffectLedger, InvalidSideEffectTransition


class ProviderOutcomeBlocked(RuntimeError):
    """Remote effect is uncertain; automatic dispatch is prohibited."""


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def stable_provider_operation_id(operation_key: str, provider: str, intent: Mapping[str, Any]) -> str:
    if not isinstance(operation_key, str) or not operation_key.strip():
        raise ValueError("operation_key is required")
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("provider is required")
    digest = hashlib.sha256(_canonical(intent).encode("utf-8")).hexdigest()
    material = "\\0".join((operation_key.strip(), provider.strip(), digest)).encode("utf-8")
    return "auren-op-" + hashlib.sha256(material).hexdigest()


class ProviderOperationLifecycle:
    """Persist intent in the existing side-effect ledger before provider dispatch.

    dispatch receives (intent, operation_id, idempotency_key). lookup receives
    (operation_id, idempotency_key) and returns status/evidence. Evidence is only
    trusted when the ledger's configured reconciliation verifier accepts it.
    """

    def __init__(self, ledger: DurableSideEffectLedger, *,
                 record_event: Callable[[Mapping[str, Any]], None] | None = None) -> None:
        if not isinstance(ledger, DurableSideEffectLedger):
            raise TypeError("the canonical DurableSideEffectLedger is required")
        if record_event is not None and not callable(record_event):
            raise TypeError("record_event must be callable")
        self.ledger = ledger
        self.record_event = record_event

    def _event(self, key: str, state: str, operation_id: str, intent_digest: str, **details: Any) -> None:
        if self.record_event is not None:
            self.record_event({
                "event_type": "provider_operation", "operation_key": key,
                "operation_id": operation_id, "intent_digest": intent_digest,
                "state": state, **details,
            })

    def execute(self, operation_key: str, provider: str, intent: Mapping[str, Any],
                dispatch: Callable[[Mapping[str, Any], str, str], Mapping[str, Any]]) -> Mapping[str, Any]:
        if not isinstance(intent, Mapping) or not callable(dispatch):
            raise TypeError("intent mapping and dispatch callable are required")
        op_id = stable_provider_operation_id(operation_key, provider, intent)
        payload = {"provider": provider, "operation_id": op_id, "intent": dict(intent)}
        decision = self.ledger.begin(operation_key, payload)
        if decision.action == "replay":
            self._event(operation_key, "completed", op_id, decision.payload_digest, replay=True)
            return dict(decision.receipt or {})
        if decision.action != "execute":
            self._event(operation_key, "blocked", op_id, decision.payload_digest, reason="reconciliation_required")
            raise ProviderOutcomeBlocked(
                f"provider operation {op_id} has uncertain outcome; reconcile before any retry"
            )
        self._event(operation_key, "dispatching", op_id, decision.payload_digest, provider=provider)
        try:
            receipt = dispatch(dict(intent), op_id, operation_key)
            if not isinstance(receipt, Mapping):
                raise TypeError("provider dispatch must return a receipt mapping")
        except Exception as exc:
            self.ledger.require_reconciliation(operation_key, f"dispatch outcome uncertain ({type(exc).__name__})")
            self._event(operation_key, "reconcile_required", op_id, decision.payload_digest,
                        error_type=type(exc).__name__)
            raise ProviderOutcomeBlocked(
                f"provider operation {op_id} outcome is uncertain; no automatic retry"
            ) from exc
        completed = self.ledger.complete(operation_key, dict(receipt))
        self._event(operation_key, "completed", op_id, completed.payload_digest,
                    receipt_digest=hashlib.sha256(_canonical(receipt).encode("utf-8")).hexdigest())
        return dict(completed.receipt or {})

    def reconcile(self, operation_key: str, provider: str, intent: Mapping[str, Any],
                  lookup: Callable[[str, str], Mapping[str, Any]] | None) -> Mapping[str, Any]:
        op_id = stable_provider_operation_id(operation_key, provider, intent)
        record = self.ledger.get(operation_key)
        if record is None:
            raise KeyError(operation_key)
        expected = {"provider": provider, "operation_id": op_id, "intent": dict(intent)}
        if record["payload_digest"] != self.ledger._digest(expected):
            raise ValueError("provider operation identity or intent does not match the durable record")
        if record["state"] == "completed":
            return dict(record["receipt"] or {})
        if record["state"] != "reconcile_required":
            raise InvalidSideEffectTransition("provider operation is not awaiting reconciliation")
        if lookup is None:
            self._event(operation_key, "blocked", op_id, record["payload_digest"],
                        reason="provider_lookup_unavailable")
            raise ProviderOutcomeBlocked(f"provider operation {op_id} requires explicit resolution")
        try:
            status = lookup(op_id, operation_key)
        except Exception as exc:
            self._event(operation_key, "blocked", op_id, record["payload_digest"],
                        reason="provider_lookup_failed", error_type=type(exc).__name__)
            raise ProviderOutcomeBlocked(f"provider operation {op_id} lookup failed; explicit resolution required") from exc
        if not isinstance(status, Mapping):
            raise ProviderOutcomeBlocked("provider lookup result is invalid; explicit resolution required")
        if status.get("status") == "completed" and isinstance(status.get("receipt"), Mapping):
            evidence_ref = status.get("evidence_ref")
            if not isinstance(evidence_ref, str) or not evidence_ref.strip():
                raise ProviderOutcomeBlocked("provider completion lacks a trusted evidence reference")
            result = self.ledger.record_reconciled_effect(
                operation_key, evidence_ref=evidence_ref, receipt=dict(status["receipt"])
            )
            self._event(operation_key, "completed", op_id, result.payload_digest,
                        evidence_ref=evidence_ref, reconciled=True)
            return dict(result.receipt or {})
        self._event(operation_key, "blocked", op_id, record["payload_digest"],
                    provider_status=str(status.get("status", "unknown")))
        raise ProviderOutcomeBlocked(
            f"provider operation {op_id} outcome is not confirmed; explicit evidence-backed resolution required"
        )
