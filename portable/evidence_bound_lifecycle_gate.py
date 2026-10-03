"""Evidence-bound gate between causal promotion and capability lifecycle.

The existing lifecycle canary is deliberately execution-oriented. This gate
keeps that authority separate and requires causal evidence before recommending
promotion. It returns a decision record and never changes lifecycle state.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .causal_capability_promotion import CausalPromotionDecision, CausalPromotionEvidence
from .capability_lifecycle import CapabilityLifecycleReceipt


@dataclass(frozen=True, slots=True)
class LifecyclePromotionDecision:
    capability_id: str
    eligible: bool
    state: str
    reasons: tuple[str, ...]
    causal_evidence_digest: str
    lifecycle_evidence_ids: tuple[str, ...]
    decision_digest: str


class EvidenceBoundLifecycleGate:
    """Require causal eligibility plus a clean bounded canary before promotion."""

    def evaluate(
        self,
        *,
        evidence: CausalPromotionEvidence,
        causal_decision: CausalPromotionDecision,
        lifecycle: CapabilityLifecycleReceipt,
    ) -> LifecyclePromotionDecision:
        reasons = []
        if evidence.capability_id != lifecycle.capability_id:
            reasons.append("capability identity mismatch")
        if not causal_decision.eligible:
            reasons.append("causal promotion gate is blocked")
        if lifecycle.state == "rolled_back":
            reasons.append("capability lifecycle is rolled back")
        if lifecycle.canary_count < 3:
            reasons.append("minimum bounded canary window not completed")
        if lifecycle.successful_canaries != lifecycle.canary_count:
            reasons.append("not all canaries passed the lifecycle safety threshold")
        if not evidence.fresh_holdout or not evidence.independent_oracle:
            reasons.append("causal evidence lacks fresh independent validation")
        if evidence.contamination_detected:
            reasons.append("causal evidence is contaminated")
        eligible = not reasons
        state = "promote" if eligible else "hold"
        payload = {
            "capability_id": evidence.capability_id,
            "eligible": eligible,
            "state": state,
            "reasons": tuple(reasons),
            "causal_evidence_digest": evidence.evidence_digest,
            "lifecycle_evidence_ids": tuple(sorted(
                str(x) for x in lifecycle.reasons
            )),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return LifecyclePromotionDecision(
            evidence.capability_id, eligible, state, tuple(reasons),
            evidence.evidence_digest, payload["lifecycle_evidence_ids"], digest,
        )


__all__ = ["LifecyclePromotionDecision", "EvidenceBoundLifecycleGate"]
