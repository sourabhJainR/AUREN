"""Externally controlled evaluation campaign protocol.

A campaign object is a content-addressed declaration, not a trust assertion.
Its signature/attestation is excluded from the campaign digest to avoid
signature self-reference and must be verified by an external trust boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Mapping


def _required_text(name: str, value: object, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{name} must be a non-empty string" if not allow_empty else f"{name} must be a string")


@dataclass(frozen=True, slots=True)
class ExternalEvaluationCampaign:
    campaign_id: str
    campaign_version: str
    arena_version: str
    corpus_digest: str
    generator_digest: str
    oracle_digest: str
    evaluator_version: str
    runtime_snapshot: str
    case_ids: tuple[str, ...]
    holdout_case_ids: tuple[str, ...]
    novel_domain_case_ids: tuple[str, ...] = ()
    unfamiliar_tool_case_ids: tuple[str, ...] = ()
    long_horizon_case_ids: tuple[str, ...] = ()
    adversarial_case_ids: tuple[str, ...] = ()
    multimodal_case_ids: tuple[str, ...] = ()
    contamination_detected: bool = False
    external_attestation: str = ""

    def __post_init__(self) -> None:
        for name, value in (
            ("campaign_id", self.campaign_id),
            ("campaign_version", self.campaign_version),
            ("arena_version", self.arena_version),
            ("corpus_digest", self.corpus_digest),
            ("generator_digest", self.generator_digest),
            ("oracle_digest", self.oracle_digest),
            ("evaluator_version", self.evaluator_version),
            ("runtime_snapshot", self.runtime_snapshot),
        ):
            _required_text(name, value)
        _required_text("external_attestation", self.external_attestation, allow_empty=True)
        if type(self.contamination_detected) is not bool:
            raise ValueError("contamination_detected must be a boolean")
        if not isinstance(self.case_ids, tuple) or not self.case_ids:
            raise ValueError("case_ids must be a non-empty tuple")
        if any(not isinstance(case_id, str) or not case_id.strip() for case_id in self.case_ids):
            raise ValueError("case_ids must contain non-empty strings")
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("duplicate case id")
        all_ids = set(self.case_ids)
        for name, values in (
            ("holdout_case_ids", self.holdout_case_ids),
            ("novel_domain_case_ids", self.novel_domain_case_ids),
            ("unfamiliar_tool_case_ids", self.unfamiliar_tool_case_ids),
            ("long_horizon_case_ids", self.long_horizon_case_ids),
            ("adversarial_case_ids", self.adversarial_case_ids),
            ("multimodal_case_ids", self.multimodal_case_ids),
        ):
            if not isinstance(values, tuple):
                raise ValueError(f"{name} must be a tuple")
            if any(not isinstance(case_id, str) or not case_id.strip() for case_id in values):
                raise ValueError(f"{name} must contain non-empty strings")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} contains duplicate case ids")
            if not set(values) <= all_ids:
                raise ValueError(f"{name} contains an unknown case id")
        if not self.holdout_case_ids:
            raise ValueError("external campaign requires holdout cases")

    @staticmethod
    def _digest(payload: Mapping[str, object]) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @property
    def campaign_digest(self) -> str:
        # External attestation/signature must be excluded to avoid a circular
        # digest dependency: the signature is produced over this digest.
        return self._digest(self.as_dict(include_digest=False))

    @property
    def holdout_coverage(self) -> float:
        return len(self.holdout_case_ids) / len(self.case_ids)

    @property
    def transfer_dimensions(self) -> tuple[str, ...]:
        dimensions = []
        for name, values in (
            ("novel_domain", self.novel_domain_case_ids),
            ("unfamiliar_tool", self.unfamiliar_tool_case_ids),
            ("long_horizon", self.long_horizon_case_ids),
            ("adversarial", self.adversarial_case_ids),
            ("multimodal", self.multimodal_case_ids),
        ):
            if values:
                dimensions.append(name)
        return tuple(dimensions)

    @property
    def has_attestation_material(self) -> bool:
        return bool(self.external_attestation.strip())

    @property
    def structurally_valid(self) -> bool:
        return (
            not self.contamination_detected
            and bool(self.corpus_digest)
            and bool(self.generator_digest)
            and bool(self.oracle_digest)
        )

    @property
    def trustworthy(self) -> bool:
        # A signature string is not a verification result. Use the attestor at
        # the point where a campaign is consumed; this object alone is untrusted.
        return False

    def as_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        payload = {
            "campaign_id": self.campaign_id,
            "campaign_version": self.campaign_version,
            "arena_version": self.arena_version,
            "corpus_digest": self.corpus_digest,
            "generator_digest": self.generator_digest,
            "oracle_digest": self.oracle_digest,
            "evaluator_version": self.evaluator_version,
            "runtime_snapshot": self.runtime_snapshot,
            "case_ids": self.case_ids,
            "holdout_case_ids": self.holdout_case_ids,
            "novel_domain_case_ids": self.novel_domain_case_ids,
            "unfamiliar_tool_case_ids": self.unfamiliar_tool_case_ids,
            "long_horizon_case_ids": self.long_horizon_case_ids,
            "adversarial_case_ids": self.adversarial_case_ids,
            "multimodal_case_ids": self.multimodal_case_ids,
            "contamination_detected": self.contamination_detected,
        }
        if include_digest:
            payload["external_attestation"] = self.external_attestation
            payload["campaign_digest"] = self.campaign_digest
        return payload


@dataclass(frozen=True, slots=True)
class CampaignOutcome:
    campaign_digest: str
    holdout_pass_rate: float
    holdout_verification_rate: float
    generalization_gap: float
    calibration_error: float
    efficiency_score: float
    failure_attribution: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required_text("campaign_digest", self.campaign_digest)
        for name in (
            "holdout_pass_rate", "holdout_verification_rate",
            "generalization_gap", "calibration_error", "efficiency_score",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a finite number between 0 and 1")
        if not isinstance(self.failure_attribution, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.failure_attribution
        ):
            raise ValueError("failure_attribution must be a tuple of non-empty strings")

    @property
    def evidence_eligible(self) -> bool:
        return self.holdout_verification_rate >= self.holdout_pass_rate and self.generalization_gap <= 1.0


@dataclass(frozen=True, slots=True)
class LearningIntervention:
    intervention_id: str
    target_capability: str
    hypothesis: str
    expected_change: str
    control_group: str = ""
    rollback_condition: str = ""

    def __post_init__(self) -> None:
        for name, value in (
            ("intervention_id", self.intervention_id),
            ("target_capability", self.target_capability),
            ("hypothesis", self.hypothesis),
            ("expected_change", self.expected_change),
        ):
            _required_text(name, value)
        _required_text("control_group", self.control_group, allow_empty=True)
        _required_text("rollback_condition", self.rollback_condition, allow_empty=True)

    @property
    def intervention_digest(self) -> str:
        payload = {
            "intervention_id": self.intervention_id,
            "target_capability": self.target_capability,
            "hypothesis": self.hypothesis,
            "expected_change": self.expected_change,
            "control_group": self.control_group,
            "rollback_condition": self.rollback_condition,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class CampaignRetestContract:
    prior_campaign_digest: str
    intervention_digest: str
    new_holdout_required: bool = True
    independence_required: bool = True

    def __post_init__(self) -> None:
        _required_text("prior_campaign_digest", self.prior_campaign_digest)
        _required_text("intervention_digest", self.intervention_digest)
        if type(self.new_holdout_required) is not bool or type(self.independence_required) is not bool:
            raise ValueError("retest requirements must be booleans")
        if self.new_holdout_required is not True or self.independence_required is not True:
            raise ValueError("retest must require a new independent holdout")


__all__ = [
    "ExternalEvaluationCampaign",
    "CampaignOutcome",
    "LearningIntervention",
    "CampaignRetestContract",
]
