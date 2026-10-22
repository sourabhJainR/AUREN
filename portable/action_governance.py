"""Deterministic, provider-neutral action authorization for AUREN.

This module is a policy decision point, not an OS sandbox. Callers must route every
consequential action through the gateway and retain a real execution boundary.
Approval verification is injected by the host and fails closed when absent.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import posixpath
import threading
import time
from typing import Any, Callable, Mapping


RISK_LEVELS = {"read", "write", "external_effect", "release"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _nonblank(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


def _normal_target(value: str) -> str:
    normalized = posixpath.normpath(value.replace("\\", "/").strip())
    return "" if normalized == "." and not value.strip() else normalized.rstrip("/")


def _matches_protected_target(target: str, root: str) -> bool:
    target_parts = tuple(part for part in _normal_target(target).strip("/").split("/") if part and part != ".")
    root_parts = tuple(part for part in _normal_target(root).strip("/").split("/") if part and part != ".")
    if not root_parts:
        return False
    return any(
        target_parts[index:index + len(root_parts)] == root_parts
        for index in range(max(0, len(target_parts) - len(root_parts) + 1))
    )


def _result_digest(result: Any) -> str:
    payload = json.dumps(
        {"type": type(result).__name__, "value": result},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=lambda value: {"__type__": type(value).__name__},
    )
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ActionRequest:
    """Minimal, privacy-conscious description of a proposed consequential action."""

    run_id: str
    action_id: str
    actor: str
    capability: str
    action: str
    target: str
    risk: str = "write"
    idempotency_key: str = ""
    evidence_refs: tuple[str, ...] = ()
    task_id: str = ""

    def validate(self) -> None:
        for name in ("run_id", "action_id", "actor", "capability", "action", "target"):
            _nonblank(getattr(self, name), name)
        if self.risk not in RISK_LEVELS:
            raise ValueError(f"unsupported risk level: {self.risk}")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("evidence_refs must be unique")
        if any(not isinstance(ref, str) or not ref.strip() for ref in self.evidence_refs):
            raise ValueError("evidence_refs must contain non-empty strings")

    def fingerprint(self) -> str:
        payload = asdict(self)
        # Preserve fingerprints for legacy requests that do not opt into task binding.
        if not payload.get("task_id"):
            payload.pop("task_id", None)
        return _digest(payload)


@dataclass(frozen=True)
class ApprovalReceipt:
    """A host-issued approval bound to exactly one request fingerprint."""

    approval_id: str
    approver: str
    request_fingerprint: str


@dataclass(frozen=True)
class ActionPolicy:
    """Small deterministic policy-as-code bundle.

    Capability allowlisting is mandatory. Protected targets and high-risk actions
    require a host-verified approval; external effects also require idempotency.
    """

    allowed_capabilities: frozenset[str]
    protected_targets: tuple[str, ...] = ()
    allowed_actions: frozenset[str] = frozenset({"read", "inspect", "test", "edit", "specialist.execute", "merge", "release", "deploy", "publish", "send", "charge", "delete_remote"})
    approval_required_actions: frozenset[str] = frozenset({"merge", "release", "deploy"})
    external_effect_actions: frozenset[str] = frozenset({"publish", "send", "charge", "delete_remote"})
    evidence_required_actions: frozenset[str] = frozenset({"merge", "release", "deploy"})
    require_idempotency_for_external_effects: bool = True
    require_durable_ledger_for_external_effects: bool = False

    def validate(self) -> None:
        if not self.allowed_capabilities:
            raise ValueError("at least one allowed capability must be configured")
        for value in self.allowed_capabilities | self.allowed_actions | self.approval_required_actions | self.external_effect_actions | self.evidence_required_actions:
            _nonblank(value, "policy action/capability")
        for target in self.protected_targets:
            _nonblank(target, "protected target")


@dataclass(frozen=True)
class PolicyVerdict:
    decision: str
    reason: str
    request_fingerprint: str
    approval_required: bool = False

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


@dataclass(frozen=True)
class GovernedResult:
    executed: bool
    verdict: PolicyVerdict
    result_digest: str = ""
    replayed: bool = False


class ActionGovernanceGateway:
    """Evaluate and mediate actions, with an in-memory tamper-evident trace.

    The trace hash chain detects modifications to the event sequence when verified,
    but is not a signature, trusted-storage guarantee, or isolation proof.
    """

    def __init__(
        self,
        policy: ActionPolicy,
        *,
        approval_verifier: Callable[[ApprovalReceipt, ActionRequest], bool] | None = None,
        clock: Callable[[], float] = time.time,
        side_effect_ledger: Any | None = None,
    ) -> None:
        policy.validate()
        self._policy = policy
        self._approval_verifier = approval_verifier
        self._clock = clock
        self._side_effect_ledger = side_effect_ledger
        self._events: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._attempted_actions: set[tuple[str, str]] = set()

    @property
    def has_durable_side_effect_ledger(self) -> bool:
        """Whether this gateway can durably fence consequential external actions."""
        return self._side_effect_ledger is not None

    def is_external_effect(self, request: Any) -> bool:
        """Classify a request using the gateway policy, not caller-supplied claims."""
        return (
            getattr(request, "risk", "") == "external_effect"
            or getattr(request, "action", "") in self._policy.external_effect_actions
        )

    def durable_side_effect_preflight(self, request: Any) -> PolicyVerdict | None:
        """Deny a consequential request when no durable intent ledger is attached.

        Adapters call this before any legacy/unbound gateway path. Authority-bound
        callers use the same check before charging their attempt budget.
        """
        if not self.is_external_effect(request) or self.has_durable_side_effect_ledger:
            return None
        fingerprint_fn = getattr(request, "fingerprint", None)
        fingerprint = fingerprint_fn() if callable(fingerprint_fn) else ""
        verdict = PolicyVerdict("deny", "durable_idempotency_ledger_required", fingerprint)
        self._append_event({
            "event": "durable_side_effect_blocked",
            "run_id": str(getattr(request, "run_id", "")),
            "action_id": str(getattr(request, "action_id", "")),
            "reason": verdict.reason,
            "request_fingerprint": fingerprint,
        })
        return verdict

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(dict(event) for event in self._events)

    def requires_approval(self, request: ActionRequest) -> bool:
        """Expose the exact policy decision used by evaluate for approval-bound execution."""
        request.validate()
        protected = _normal_target(request.target)
        protected_hit = any(
            _matches_protected_target(protected, root)
            for root in self._policy.protected_targets
        )
        return (
            request.action in self._policy.approval_required_actions
            or protected_hit
            or request.risk == "release"
        )

    def evaluate(
        self,
        request: ActionRequest,
        *,
        approval: ApprovalReceipt | None = None,
    ) -> PolicyVerdict:
        request.validate()
        fingerprint = request.fingerprint()
        protected = _normal_target(request.target)
        needs_approval = self.requires_approval(request)

        reason = "allowed"
        decision = "allow"
        if request.capability not in self._policy.allowed_capabilities:
            decision, reason = "deny", "capability_not_allowlisted"
        elif request.action not in self._policy.allowed_actions:
            decision, reason = "deny", "action_not_allowlisted"
        elif request.action in self._policy.evidence_required_actions and not request.evidence_refs:
            decision, reason = "deny", "required_evidence_missing"
        elif (
            self._policy.require_idempotency_for_external_effects
            and (request.risk == "external_effect" or request.action in self._policy.external_effect_actions)
            and not request.idempotency_key.strip()
        ):
            decision, reason = "deny", "idempotency_key_required"
        elif needs_approval:
            verified = False
            if approval is not None and self._approval_verifier is not None:
                try:
                    verified = (
                        approval.request_fingerprint == fingerprint
                        and bool(approval.approval_id.strip())
                        and bool(approval.approver.strip())
                        and self._approval_verifier(approval, request)
                    )
                except Exception:
                    verified = False
            if not verified:
                decision, reason = "approval_required", "trusted_approval_missing_or_invalid"

        verdict = PolicyVerdict(
            decision=decision,
            reason=reason,
            request_fingerprint=fingerprint,
            approval_required=needs_approval,
        )
        self._append_event({
            "event": "policy_verdict",
            "run_id": request.run_id,
            "action_id": request.action_id,
            "capability": request.capability,
            "action": request.action,
            "target_digest": _digest(protected),
            "decision": verdict.decision,
            "reason": verdict.reason,
            "request_fingerprint": fingerprint,
        })
        return verdict

    def execute(
        self,
        request: ActionRequest,
        handler: Callable[[], Any],
        *,
        approval: ApprovalReceipt | None = None,
    ) -> GovernedResult:
        """Invoke the handler only after allow; each run/action identity is single-use.

        The caller must not execute the same action outside this gateway. External
        effects should use the request's idempotency key at the downstream service.
        """
        verdict = self.evaluate(request, approval=approval)
        if not verdict.allowed:
            return GovernedResult(executed=False, verdict=verdict)

        identity = (request.run_id, request.action_id)
        with self._lock:
            if identity in self._attempted_actions:
                blocked = PolicyVerdict(
                    "deny", "duplicate_action_identity", verdict.request_fingerprint,
                    verdict.approval_required,
                )
                self._append_event({
                    "event": "duplicate_action_blocked", "run_id": request.run_id,
                    "action_id": request.action_id, "request_fingerprint": verdict.request_fingerprint,
                })
                return GovernedResult(executed=False, verdict=blocked)
            # Reserve the identity before touching durable state so concurrent callers
            # cannot strand a second ledger key for an action that will never run.
            self._attempted_actions.add(identity)

        external_effect = (
            request.risk == "external_effect"
            or request.action in self._policy.external_effect_actions
        )
        if external_effect and self._policy.require_durable_ledger_for_external_effects and self._side_effect_ledger is None:
            blocked = PolicyVerdict(
                "deny", "durable_idempotency_ledger_required", verdict.request_fingerprint,
                verdict.approval_required,
            )
            with self._lock:
                self._attempted_actions.discard(identity)
            self._append_event({
                "event": "durable_side_effect_blocked", "run_id": request.run_id,
                "action_id": request.action_id, "reason": blocked.reason,
                "request_fingerprint": verdict.request_fingerprint,
            })
            return GovernedResult(executed=False, verdict=blocked)

        # Persist the intent before a consequential handler can run. A replay returns
        # the immutable receipt; an ambiguous prior execution is blocked for operator
        # reconciliation rather than being invoked a second time.
        if external_effect and self._side_effect_ledger is not None:
            try:
                decision = self._side_effect_ledger.begin(
                    request.idempotency_key,
                    {
                        "request_fingerprint": verdict.request_fingerprint,
                        "run_id": request.run_id,
                        "task_id": request.task_id,
                        "action_id": request.action_id,
                        "actor": request.actor,
                        "capability": request.capability,
                        "action": request.action,
                        "target": request.target,
                        "evidence_refs": list(request.evidence_refs),
                    },
                )
            except Exception as exc:
                with self._lock:
                    self._attempted_actions.discard(identity)
                blocked = PolicyVerdict(
                    "deny", "durable_idempotency_rejected", verdict.request_fingerprint,
                    verdict.approval_required,
                )
                self._append_event({
                    "event": "durable_side_effect_blocked", "run_id": request.run_id,
                    "action_id": request.action_id, "reason": blocked.reason,
                    "error_type": type(exc).__name__,
                    "request_fingerprint": verdict.request_fingerprint,
                })
                return GovernedResult(executed=False, verdict=blocked)
            if decision.action == "replay":
                with self._lock:
                    self._attempted_actions.discard(identity)
                receipt = decision.receipt or {}
                digest = str(receipt.get("result_digest", ""))
                self._append_event({
                    "event": "durable_side_effect_replayed", "run_id": request.run_id,
                    "action_id": request.action_id, "request_fingerprint": verdict.request_fingerprint,
                    "result_digest": digest,
                })
                return GovernedResult(executed=False, verdict=verdict, result_digest=digest, replayed=True)
            if decision.action != "execute":
                with self._lock:
                    self._attempted_actions.discard(identity)
                blocked = PolicyVerdict(
                    "deny", "side_effect_reconciliation_required", verdict.request_fingerprint,
                    verdict.approval_required,
                )
                self._append_event({
                    "event": "durable_side_effect_blocked", "run_id": request.run_id,
                    "action_id": request.action_id, "reason": blocked.reason,
                    "side_effect_state": str(getattr(decision, "state", "")),
                    "request_fingerprint": verdict.request_fingerprint,
                })
                return GovernedResult(executed=False, verdict=blocked)

        self._append_event({
            "event": "action_started",
            "run_id": request.run_id,
            "action_id": request.action_id,
            "request_fingerprint": verdict.request_fingerprint,
        })
        try:
            result = handler()
        except Exception as exc:
            if external_effect and self._side_effect_ledger is not None:
                try:
                    self._side_effect_ledger.require_reconciliation(
                        request.idempotency_key,
                        f"handler raised {type(exc).__name__}; external outcome may be ambiguous",
                    )
                except Exception:
                    # Preserve the original handler exception; the ledger remains fail-closed.
                    pass
            self._append_event({
                "event": "action_failed",
                "run_id": request.run_id,
                "action_id": request.action_id,
                "request_fingerprint": verdict.request_fingerprint,
                "error_type": type(exc).__name__,
            })
            raise
        result_digest = _result_digest(result)
        if external_effect and self._side_effect_ledger is not None:
            # If receipt persistence fails after the remote effect, leave the intent
            # unresolved. The next attempt will require reconciliation, never replay.
            self._side_effect_ledger.complete(
                request.idempotency_key,
                {"result_digest": result_digest, "request_fingerprint": verdict.request_fingerprint},
            )
        self._append_event({
            "event": "action_completed",
            "run_id": request.run_id,
            "action_id": request.action_id,
            "request_fingerprint": verdict.request_fingerprint,
            "result_digest": result_digest,
        })
        return GovernedResult(executed=True, verdict=verdict, result_digest=result_digest)

    def verify_trace(self) -> dict[str, Any]:
        with self._lock:
            rows = [dict(row) for row in self._events]
        previous = ""
        for index, row in enumerate(rows, start=1):
            if row.get("sequence") != index or row.get("previous_hash", "") != previous:
                return {"passed": False, "events": len(rows), "reason": "sequence_or_link_mismatch"}
            unsigned = dict(row)
            supplied = unsigned.pop("hash", None)
            if supplied != _digest(unsigned):
                return {"passed": False, "events": len(rows), "reason": "hash_mismatch"}
            previous = str(supplied)
        return {"passed": True, "events": len(rows), "head": previous}

    def _append_event(self, fields: Mapping[str, Any]) -> None:
        with self._lock:
            row = {
                "sequence": len(self._events) + 1,
                "timestamp": self._clock(),
                "previous_hash": self._events[-1]["hash"] if self._events else "",
                **dict(fields),
            }
            row["hash"] = _digest(row)
            self._events.append(row)


__all__ = [
    "ActionGovernanceGateway",
    "ActionPolicy",
    "ActionRequest",
    "ApprovalReceipt",
    "GovernedResult",
    "PolicyVerdict",
]
