"""Explicit execution boundary for approved learning interventions.

This is the first component allowed to invoke an intervention executor, and it
does so only with an external approval token bound to the exact immutable
decision and intervention digests. Execution, verification, rollback, and
promotion are separate callbacks. No implicit promotion is possible.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib, json
from typing import Callable, Any

from .evidence_to_learning_intervention import LearningInterventionDecision


@dataclass(frozen=True, slots=True)
class InterventionAuthorization:
    decision_digest: str
    intervention_digest: str
    approval_token: str
    approver: str

    def __post_init__(self) -> None:
        if not self.decision_digest.strip() or not self.intervention_digest.strip():
            raise ValueError("decision and intervention digests are required")
        if not self.approval_token.strip() or not self.approver.strip():
            raise ValueError("external approval token and approver are required")


@dataclass(frozen=True, slots=True)
class InterventionExecutionReceipt:
    execution_id: str
    decision_digest: str
    intervention_digest: str
    authorization_digest: str
    applied: bool
    verified: bool
    rolled_back: bool
    promotion_blocked: bool
    evidence_ids: tuple[str, ...]
    error: str
    receipt_digest: str

    @property
    def safe_terminal(self) -> bool:
        return self.verified or self.rolled_back


class ClosedLoopInterventionExecutor:
    """Execute an explicitly approved intervention with mandatory verification."""

    def __init__(
        self,
        *,
        apply: Callable[[Any], Any],
        verify: Callable[[Any], tuple[bool, tuple[str, ...]]],
        rollback: Callable[[Any], Any],
    ) -> None:
        self.apply, self.verify, self.rollback = apply, verify, rollback

    @staticmethod
    def _digest(value: object) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    def run(
        self,
        decision: LearningInterventionDecision,
        authorization: InterventionAuthorization,
        *,
        execution_id: str,
    ) -> InterventionExecutionReceipt:
        if not execution_id.strip():
            raise ValueError("execution_id is required")
        if not decision.executable or decision.intervention is None or decision.retest_contract is None:
            raise ValueError("decision is not executable")
        expected = decision.intervention.intervention_digest
        if authorization.decision_digest != decision.decision_digest:
            raise ValueError("authorization decision digest mismatch")
        if authorization.intervention_digest != expected:
            raise ValueError("authorization intervention digest mismatch")
        if not authorization.approval_token.strip():
            raise ValueError("approval token is required")

        auth_digest = self._digest({
            "decision_digest": authorization.decision_digest,
            "intervention_digest": authorization.intervention_digest,
            "approver": authorization.approver,
        })
        applied = False
        verified = False
        rolled_back = False
        evidence: tuple[str, ...] = ()
        error = ""
        artifact = None
        try:
            artifact = self.apply(decision.intervention)
            applied = True
            verified, evidence = self.verify(artifact)
            evidence = tuple(dict.fromkeys(str(x) for x in evidence if str(x)))
            if not verified:
                self.rollback(artifact)
                rolled_back = True
                error = "post-execution verification failed; rollback invoked"
        except Exception as exc:
            error = str(exc)
            if applied:
                try:
                    self.rollback(artifact)
                    rolled_back = True
                except Exception as rollback_error:
                    error = f"{error}; rollback failed: {rollback_error}"

        payload = {
            "execution_id": execution_id,
            "decision_digest": decision.decision_digest,
            "intervention_digest": expected,
            "authorization_digest": auth_digest,
            "applied": applied,
            "verified": verified,
            "rolled_back": rolled_back,
            "promotion_blocked": True,
            "evidence_ids": evidence,
            "error": error,
        }
        return InterventionExecutionReceipt(
            execution_id, decision.decision_digest, expected, auth_digest,
            applied, verified, rolled_back, True, evidence, error,
            self._digest(payload),
        )


__all__ = ["InterventionAuthorization", "InterventionExecutionReceipt", "ClosedLoopInterventionExecutor"]
