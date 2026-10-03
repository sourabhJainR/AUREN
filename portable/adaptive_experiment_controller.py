"""Adaptive, lineage-preserving experiment control for learning interventions.

This module aggregates independently executed campaign replications and emits
bounded evidence/decision records. It never executes an intervention and never
mutates capability state.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable, Mapping

from .external_evaluation_campaign import CampaignRetestContract, LearningIntervention


@dataclass(frozen=True, slots=True)
class ExperimentAssignment:
    experiment_id: str
    intervention_digest: str
    unit_id: str
    group: str
    campaign_digest: str

    def __post_init__(self) -> None:
        if self.group not in {"control", "treatment"}:
            raise ValueError("group must be control or treatment")
        for name in ("experiment_id", "intervention_digest", "unit_id", "campaign_digest"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")

    @property
    def assignment_digest(self) -> str:
        payload = {k: getattr(self, k) for k in (
            "experiment_id", "intervention_digest", "unit_id", "group", "campaign_digest"
        )}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ExperimentObservation:
    assignment: ExperimentAssignment
    score: float
    verified: bool
    fresh_holdout: bool
    independent_oracle: bool
    contamination_detected: bool
    domain: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.score) <= 1.0:
            raise ValueError("score must be between 0 and 1")
        if not self.fresh_holdout or not self.independent_oracle:
            raise ValueError("observations require a fresh holdout and independent oracle")
        if self.contamination_detected:
            raise ValueError("contaminated observations cannot enter an experiment")


@dataclass(frozen=True, slots=True)
class ExperimentReplication:
    campaign_digest: str
    domain: str
    control_scores: tuple[float, ...]
    treatment_scores: tuple[float, ...]
    holdout_scores: tuple[float, ...]
    verified: bool
    independent_oracle: bool
    contamination_detected: bool

    def __post_init__(self) -> None:
        if not self.campaign_digest.strip() or not self.domain.strip():
            raise ValueError("campaign_digest and domain are required")
        if not self.control_scores or not self.treatment_scores or not self.holdout_scores:
            raise ValueError("each replication needs control, treatment, and holdout observations")
        for values in (self.control_scores, self.treatment_scores, self.holdout_scores):
            if any(not 0.0 <= float(v) <= 1.0 for v in values):
                raise ValueError("scores must be between 0 and 1")
        if not self.verified or not self.independent_oracle:
            raise ValueError("replication requires verified independent-oracle evidence")
        if self.contamination_detected:
            raise ValueError("contaminated replication cannot be admitted")

    @staticmethod
    def _mean(values: tuple[float, ...]) -> float:
        return sum(values) / len(values)

    @property
    def treatment_lift(self) -> float:
        return self._mean(self.treatment_scores) - self._mean(self.control_scores)

    @property
    def holdout_lift(self) -> float:
        return self._mean(self.holdout_scores) - self._mean(self.control_scores)


@dataclass(frozen=True, slots=True)
class AdaptiveExperimentResult:
    experiment_id: str
    intervention_digest: str
    replication_count: int
    observation_count: int
    aggregated_treatment_lift: float
    aggregated_holdout_lift: float
    replication_consistency: float
    regression_detected: bool
    stop: bool
    rollback_recommended: bool
    evidence_sufficient: bool
    reasons: tuple[str, ...]

    @property
    def result_digest(self) -> str:
        payload = {
            "experiment_id": self.experiment_id,
            "intervention_digest": self.intervention_digest,
            "replication_count": self.replication_count,
            "observation_count": self.observation_count,
            "aggregated_treatment_lift": self.aggregated_treatment_lift,
            "aggregated_holdout_lift": self.aggregated_holdout_lift,
            "replication_consistency": self.replication_consistency,
            "regression_detected": self.regression_detected,
            "stop": self.stop,
            "rollback_recommended": self.rollback_recommended,
            "evidence_sufficient": self.evidence_sufficient,
            "reasons": self.reasons,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ExperimentController:
    """Assign, aggregate, and stop experiments using explicit evidence rules."""

    def __init__(
        self,
        *,
        minimum_replications: int = 3,
        minimum_observations: int = 6,
        minimum_lift: float = 0.05,
        minimum_holdout_lift: float = 0.02,
        maximum_regression: float = 0.02,
        minimum_consistency: float = 2 / 3,
    ) -> None:
        if minimum_replications < 1 or minimum_observations < 1:
            raise ValueError("minimum sample requirements must be positive")
        if not 0 <= minimum_consistency <= 1:
            raise ValueError("minimum_consistency must be between 0 and 1")
        if not 0 <= maximum_regression <= 1:
            raise ValueError("maximum_regression must be between 0 and 1")
        self.minimum_replications = minimum_replications
        self.minimum_observations = minimum_observations
        self.minimum_lift = minimum_lift
        self.minimum_holdout_lift = minimum_holdout_lift
        self.maximum_regression = maximum_regression
        self.minimum_consistency = minimum_consistency

    def assign(self, *, experiment_id: str, intervention: LearningIntervention,
               unit_id: str, campaign_digest: str, treatment: bool) -> ExperimentAssignment:
        if not experiment_id.strip() or not unit_id.strip() or not campaign_digest.strip():
            raise ValueError("experiment, unit, and campaign identities are required")
        group = "treatment" if treatment else "control"
        # Assignment is content-addressed so the same unit cannot silently switch groups.
        return ExperimentAssignment(
            experiment_id, intervention.intervention_digest, unit_id, group, campaign_digest
        )

    def validate_assignments(self, assignments: Iterable[ExperimentAssignment]) -> None:
        seen: dict[str, str] = {}
        for item in assignments:
            prior = seen.get(item.unit_id)
            if prior and prior != item.group:
                raise ValueError("treatment leakage: unit assigned to both groups")
            seen[item.unit_id] = item.group

    def aggregate(
        self,
        *,
        experiment_id: str,
        intervention: LearningIntervention,
        replications: tuple[ExperimentReplication, ...],
        retest_contract: CampaignRetestContract,
        baseline_score: float,
    ) -> AdaptiveExperimentResult:
        if not replications:
            return AdaptiveExperimentResult(
                experiment_id, intervention.intervention_digest, 0, 0, 0.0, 0.0, 0.0,
                False, True, False, False, ("no replications",)
            )
        if not 0.0 <= baseline_score <= 1.0:
            raise ValueError("baseline_score must be between 0 and 1")
        if retest_contract.intervention_digest != intervention.intervention_digest:
            raise ValueError("retest contract belongs to a different intervention")
        campaigns = {r.campaign_digest for r in replications}
        if len(campaigns) != len(replications):
            raise ValueError("replications must use distinct campaigns")
        if retest_contract.prior_campaign_digest in campaigns:
            raise ValueError("retest campaign must differ from the prior campaign")

        treatment = sum(r.treatment_lift for r in replications) / len(replications)
        holdout = sum(r.holdout_lift for r in replications) / len(replications)
        consistent = sum(r.treatment_lift >= self.minimum_lift for r in replications) / len(replications)
        regression = any(r.holdout_lift < -self.maximum_regression for r in replications)
        observations = sum(len(r.control_scores) + len(r.treatment_scores) + len(r.holdout_scores) for r in replications)
        sufficient = (
            len(replications) >= self.minimum_replications
            and observations >= self.minimum_observations
            and consistent >= self.minimum_consistency
            and treatment >= self.minimum_lift
            and holdout >= self.minimum_holdout_lift
            and not regression
        )
        reasons = []
        if len(replications) < self.minimum_replications:
            reasons.append("insufficient independent replications")
        if observations < self.minimum_observations:
            reasons.append("insufficient observations")
        if consistent < self.minimum_consistency:
            reasons.append("replication effect is inconsistent")
        if treatment < self.minimum_lift:
            reasons.append("aggregated treatment lift below threshold")
        if holdout < self.minimum_holdout_lift:
            reasons.append("aggregated holdout lift below threshold")
        if regression:
            reasons.append("holdout regression detected")
        if not reasons:
            reasons.append("minimum evidence criteria met")
        return AdaptiveExperimentResult(
            experiment_id, intervention.intervention_digest, len(replications), observations,
            treatment, holdout, consistent, regression, True, regression, sufficient, tuple(reasons)
        )


__all__ = [
    "ExperimentAssignment", "ExperimentObservation", "ExperimentReplication",
    "AdaptiveExperimentResult", "ExperimentController",
]
