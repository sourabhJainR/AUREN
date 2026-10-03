"""Causal evidence gate for capability promotion.

A capability improvement is not attributed to a learning intervention merely
because a post-test score increased. This gate requires a fresh holdout,
treatment/control evidence, independent attribution evidence, and no known
contamination. It returns a decision record only; execution and lifecycle
mutation remain outside this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True, slots=True)
class CausalPromotionEvidence:
    capability_id: str
    intervention_id: str
    baseline_score: float
    control_score: float
    treatment_score: float
    holdout_score: float
    attribution_confidence: float
    fresh_holdout: bool
    independent_oracle: bool
    contamination_detected: bool
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "baseline_score", "control_score", "treatment_score",
            "holdout_score", "attribution_confidence",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if not self.capability_id.strip() or not self.intervention_id.strip():
            raise ValueError("capability_id and intervention_id are required")
        if not self.evidence_ids or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("unique independent evidence ids are required")

    @property
    def treatment_lift(self) -> float:
        return self.treatment_score - self.control_score

    @property
    def holdout_lift(self) -> float:
        return self.holdout_score - self.baseline_score

    @property
    def evidence_digest(self) -> str:
        payload = {
            "capability_id": self.capability_id,
            "intervention_id": self.intervention_id,
            "baseline_score": self.baseline_score,
            "control_score": self.control_score,
            "treatment_score": self.treatment_score,
            "holdout_score": self.holdout_score,
            "attribution_confidence": self.attribution_confidence,
            "fresh_holdout": self.fresh_holdout,
            "independent_oracle": self.independent_oracle,
            "contamination_detected": self.contamination_detected,
            "evidence_ids": self.evidence_ids,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class CausalPromotionDecision:
    eligible: bool
    reasons: tuple[str, ...]
    evidence_digest: str

    @property
    def state(self) -> str:
        return "eligible" if self.eligible else "blocked"


class CausalCapabilityPromotionGate:
    """Evaluate attribution evidence without mutating capability state."""

    def __init__(
        self,
        *,
        minimum_treatment_lift: float = 0.05,
        minimum_holdout_lift: float = 0.02,
        minimum_attribution_confidence: float = 0.80,
    ) -> None:
        if not 0 <= minimum_treatment_lift <= 1:
            raise ValueError("minimum_treatment_lift must be between 0 and 1")
        if not 0 <= minimum_holdout_lift <= 1:
            raise ValueError("minimum_holdout_lift must be between 0 and 1")
        if not 0 <= minimum_attribution_confidence <= 1:
            raise ValueError("minimum_attribution_confidence must be between 0 and 1")
        self.minimum_treatment_lift = minimum_treatment_lift
        self.minimum_holdout_lift = minimum_holdout_lift
        self.minimum_attribution_confidence = minimum_attribution_confidence

    def evaluate(self, evidence: CausalPromotionEvidence) -> CausalPromotionDecision:
        reasons = []
        if evidence.contamination_detected:
            reasons.append("contamination detected")
        if not evidence.fresh_holdout:
            reasons.append("fresh independent holdout required")
        if not evidence.independent_oracle:
            reasons.append("independent oracle required")
        if evidence.treatment_lift < self.minimum_treatment_lift:
            reasons.append("treatment lift below attribution threshold")
        if evidence.holdout_lift < self.minimum_holdout_lift:
            reasons.append("fresh holdout did not improve over baseline")
        if evidence.attribution_confidence < self.minimum_attribution_confidence:
            reasons.append("attribution confidence below threshold")
        if evidence.control_score > evidence.baseline_score + 0.02:
            reasons.append("control group regressed baseline")
        return CausalPromotionDecision(
            not reasons, tuple(reasons), evidence.evidence_digest,
        )


__all__ = [
    "CausalPromotionEvidence",
    "CausalPromotionDecision",
    "CausalCapabilityPromotionGate",
]
