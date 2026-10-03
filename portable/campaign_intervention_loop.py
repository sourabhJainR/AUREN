"""Closed-loop campaign intervention planner.

Converts independently observed failures into explicit, bounded learning
hypotheses and retest contracts. It does not execute interventions or mutate
capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from .external_evaluation_campaign import CampaignOutcome, CampaignRetestContract, LearningIntervention


@dataclass(frozen=True, slots=True)
class FailurePattern:
    category: str
    evidence_ids: tuple[str, ...]
    frequency: int
    observed_impact: float

    def __post_init__(self) -> None:
        if not self.category.strip() or not self.evidence_ids:
            raise ValueError("failure category and evidence are required")
        if self.frequency < 1:
            raise ValueError("frequency must be positive")
        if not 0.0 <= self.observed_impact <= 1.0:
            raise ValueError("observed_impact must be between 0 and 1")


class CampaignInterventionPlanner:
    """Turn measured failure patterns into falsifiable intervention hypotheses."""

    def propose(
        self,
        *,
        campaign_digest: str,
        outcome: CampaignOutcome,
        failures: tuple[FailurePattern, ...],
        target_capability: str,
    ) -> tuple[LearningIntervention, ...]:
        if not campaign_digest.strip() or not target_capability.strip():
            raise ValueError("campaign and capability identities are required")
        if campaign_digest != outcome.campaign_digest:
            raise ValueError("outcome belongs to a different campaign")
        proposals = []
        for index, failure in enumerate(failures, 1):
            hypothesis = (
                f"Changing {target_capability} to address {failure.category} "
                f"will reduce observed failure impact on a fresh holdout."
            )
            expected = f"reduce_{failure.category}_impact_below_{max(0.0, failure.observed_impact - 0.05):.2f}"
            proposals.append(
                LearningIntervention(
                    f"{campaign_digest[:12]}-i{index}",
                    target_capability,
                    hypothesis,
                    expected,
                    control_group="unchanged-policy",
                    rollback_condition=f"fresh holdout regression or increased {failure.category}",
                )
            )
        return tuple(proposals)

    def retest_contract(
        self,
        campaign_digest: str,
        intervention: LearningIntervention,
    ) -> CampaignRetestContract:
        return CampaignRetestContract(
            campaign_digest,
            intervention.intervention_digest,
            new_holdout_required=True,
            independence_required=True,
        )


__all__ = ["FailurePattern", "CampaignInterventionPlanner"]
