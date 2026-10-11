"""Causal evidence gate for capability promotion.

A capability improvement is not attributed to a learning intervention merely
because a post-test score increased. The gate requires fresh holdout,
treatment/control evidence, independent attribution, and no contamination.
It returns a decision record only; lifecycle mutation remains outside this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math


def _required_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


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
        _required_text("capability_id", self.capability_id)
        _required_text("intervention_id", self.intervention_id)
        for name in (
            "baseline_score", "control_score", "treatment_score",
            "holdout_score", "attribution_confidence",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number between 0 and 1")
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        for name in ("fresh_holdout", "independent_oracle", "contamination_detected"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if not isinstance(self.evidence_ids, tuple) or not self.evidence_ids:
            raise ValueError("unique independent evidence ids are required")
        if any(not isinstance(item, str) or not item.strip() for item in self.evidence_ids):
            raise ValueError("evidence ids must be non-empty strings")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
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
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class CausalPromotionDecision:
    eligible: bool
    reasons: tuple[str, ...]
    evidence_digest: str

    def __post_init__(self) -> None:
        if type(self.eligible) is not bool:
            raise ValueError("eligible must be a boolean")
        if not isinstance(self.reasons, tuple) or any(
            not isinstance(reason, str) or not reason.strip() for reason in self.reasons
        ):
            raise ValueError("reasons must be a tuple of non-empty strings")
        _required_text("evidence_digest", self.evidence_digest)
        if self.eligible and self.reasons:
            raise ValueError("eligible decision cannot contain blocking reasons")
        if not self.eligible and not self.reasons:
            raise ValueError("blocked decision must contain at least one reason")

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
        for name, value in (
            ("minimum_treatment_lift", minimum_treatment_lift),
            ("minimum_holdout_lift", minimum_holdout_lift),
            ("minimum_attribution_confidence", minimum_attribution_confidence),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be a finite number between 0 and 1")
        self.minimum_treatment_lift = minimum_treatment_lift
        self.minimum_holdout_lift = minimum_holdout_lift
        self.minimum_attribution_confidence = minimum_attribution_confidence

    def evaluate(self, evidence: CausalPromotionEvidence) -> CausalPromotionDecision:
        if not isinstance(evidence, CausalPromotionEvidence):
            raise TypeError("CausalPromotionEvidence is required")
        reasons = []
        if evidence.contamination_detected is True:
            reasons.append("contamination detected")
        if evidence.fresh_holdout is not True:
            reasons.append("fresh independent holdout required")
        if evidence.independent_oracle is not True:
            reasons.append("independent oracle required")
        if evidence.treatment_lift < self.minimum_treatment_lift:
            reasons.append("treatment lift below attribution threshold")
        if evidence.holdout_lift < self.minimum_holdout_lift:
            reasons.append("fresh holdout did not improve over baseline")
        if evidence.attribution_confidence < self.minimum_attribution_confidence:
            reasons.append("attribution confidence below threshold")
        if evidence.control_score > evidence.baseline_score + 0.02:
            reasons.append("control group regressed baseline")
        return CausalPromotionDecision(not reasons, tuple(reasons), evidence.evidence_digest)


__all__ = ["CausalPromotionEvidence", "CausalPromotionDecision", "CausalCapabilityPromotionGate"]
