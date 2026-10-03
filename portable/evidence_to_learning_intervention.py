"""Evidence-bound learning intervention controller.

Converts trustworthy paired causal outcomes plus longitudinal transfer evidence
into a bounded intervention proposal. The controller is deliberately proposal-
only: it cannot execute a change, mutate a capability registry, or promote a
lifecycle state.

The key invariant is that learning is attributable to independently verified
counterfactual evidence and must be re-tested on a fresh independent holdout.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .counterfactual_outcome_attribution import CounterfactualAttribution
from .external_evaluation_campaign import CampaignRetestContract, LearningIntervention
from .longitudinal_transfer_evaluator import LongitudinalTransferProfile


@dataclass(frozen=True, slots=True)
class LearningInterventionDecision:
    """Immutable recommendation for the next learning experiment."""

    capability: str
    action: str
    intervention: LearningIntervention | None
    retest_contract: CampaignRetestContract | None
    attribution_digest: str
    transfer_digest: str
    paired_cases: int
    causal_lift: float
    transfer_rate: float
    rollback_condition: str
    rationale: tuple[str, ...]
    decision_digest: str

    def __post_init__(self) -> None:
        if not self.capability.strip():
            raise ValueError("capability is required")
        if self.action not in {"learn", "rollback", "insufficient-evidence"}:
            raise ValueError("unsupported learning action")
        if self.paired_cases < 0:
            raise ValueError("paired_cases must be non-negative")

    @property
    def executable(self) -> bool:
        """Whether the decision has a fully specified, fresh-holdout retest."""
        return (
            self.action == "learn"
            and self.intervention is not None
            and self.retest_contract is not None
            and self.retest_contract.new_holdout_required
            and self.retest_contract.independence_required
        )


class EvidenceToLearningInterventionController:
    """Derive bounded learning proposals from causal and transfer evidence."""

    def __init__(self, *, minimum_pairs: int = 3, minimum_lift: float = 0.05) -> None:
        if minimum_pairs < 1:
            raise ValueError("minimum_pairs must be positive")
        if not 0.0 <= minimum_lift <= 1.0:
            raise ValueError("minimum_lift must be between 0 and 1")
        self.minimum_pairs = minimum_pairs
        self.minimum_lift = minimum_lift

    @staticmethod
    def _digest(payload: object) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    def decide(
        self,
        *,
        capability: str,
        attribution: CounterfactualAttribution,
        transfer: LongitudinalTransferProfile,
        prior_campaign_digest: str,
        failure_category: str = "observed-generalization-failure",
    ) -> LearningInterventionDecision:
        if not capability.strip():
            raise ValueError("capability is required")
        if not prior_campaign_digest.strip():
            raise ValueError("prior campaign digest is required")
        if attribution.experiment_id.strip() == "":
            raise ValueError("attribution experiment identity is required")
        if not transfer.capability.strip() or transfer.capability != capability:
            raise ValueError("transfer evidence must identify the same capability")
        if not attribution.trustworthy:
            return self._insufficient(
                capability, attribution, transfer,
                "counterfactual attribution is not trustworthy",
            )
        paired = len(attribution.case_ids)
        if paired < self.minimum_pairs:
            return self._insufficient(
                capability, attribution, transfer,
                f"only {paired} paired cases; {self.minimum_pairs} required",
            )
        if transfer.independent_domains < 2 or not transfer.trustworthy:
            return self._insufficient(
                capability, attribution, transfer,
                "longitudinal transfer lacks trustworthy cross-domain holdout evidence",
            )
        if transfer.observations and any(
            not o.oracle_independent or o.contaminated for o in transfer.observations
        ):
            return self._insufficient(
                capability, attribution, transfer,
                "transfer evidence contains non-independent or contaminated observations",
            )

        lift = attribution.quality_lift
        transfer_rate = transfer.novel_domain_pass_rate
        if lift < 0:
            rollback = (
                f"rollback {capability} if fresh holdout confirms negative causal lift "
                f"({lift:.4f}) or transfer degrades"
            )
            rationale = (
                "paired treatment underperformed control",
                "independent cross-domain evidence does not justify learning from the regression",
            )
            return self._decision(
                capability, "rollback", None, None, attribution, transfer,
                rollback, rationale,
            )
        if lift < self.minimum_lift:
            return self._insufficient(
                capability, attribution, transfer,
                f"causal lift {lift:.4f} is below minimum {self.minimum_lift:.4f}",
            )

        category = failure_category.strip() or "observed-generalization-failure"
        intervention_seed = self._digest({
            "capability": capability,
            "attribution": attribution.evidence_digest,
            "transfer": transfer.profile_digest,
            "category": category,
        })
        intervention = LearningIntervention(
            intervention_id=f"evidence-learn-{intervention_seed[:16]}",
            target_capability=capability,
            hypothesis=(
                f"Apply a bounded change to {capability} addressing {category}; "
                "causal lift should persist on a fresh independent holdout."
            ),
            expected_change=(
                f"retain_causal_lift_at_least_{self.minimum_lift:.2f}_and_preserve_transfer"
            ),
            control_group="unchanged-capability",
            rollback_condition=(
                "fresh independent holdout regression, contamination, "
                "or loss of cross-domain transfer"
            ),
        )
        retest = CampaignRetestContract(
            prior_campaign_digest=prior_campaign_digest,
            intervention_digest=intervention.intervention_digest,
            new_holdout_required=True,
            independence_required=True,
        )
        rationale = (
            f"paired causal lift {lift:.4f} meets minimum {self.minimum_lift:.4f}",
            f"transfer spans {transfer.independent_domains} independent novel domains",
            "learning is bounded to one falsifiable intervention",
            "execution and promotion remain outside this controller",
        )
        return self._decision(
            capability, "learn", intervention, retest, attribution, transfer,
            intervention.rollback_condition, rationale,
        )

    def _insufficient(
        self,
        capability: str,
        attribution: CounterfactualAttribution,
        transfer: LongitudinalTransferProfile,
        reason: str,
    ) -> LearningInterventionDecision:
        return self._decision(
            capability, "insufficient-evidence", None, None,
            attribution, transfer, "collect fresh independent paired holdout evidence",
            (reason, "no capability mutation is permitted"),
        )

    def _decision(
        self,
        capability: str,
        action: str,
        intervention: LearningIntervention | None,
        retest: CampaignRetestContract | None,
        attribution: CounterfactualAttribution,
        transfer: LongitudinalTransferProfile,
        rollback: str,
        rationale: tuple[str, ...],
    ) -> LearningInterventionDecision:
        payload = {
            "capability": capability,
            "action": action,
            "intervention_digest": intervention.intervention_digest if intervention else "",
            "retest": {
                "prior_campaign_digest": retest.prior_campaign_digest,
                "intervention_digest": retest.intervention_digest,
            } if retest else None,
            "attribution_digest": attribution.evidence_digest,
            "transfer_digest": transfer.profile_digest,
            "paired_cases": len(attribution.case_ids),
            "causal_lift": attribution.quality_lift,
            "transfer_rate": transfer.novel_domain_pass_rate,
            "rollback_condition": rollback,
            "rationale": rationale,
        }
        return LearningInterventionDecision(
            capability, action, intervention, retest,
            attribution.evidence_digest, transfer.profile_digest,
            len(attribution.case_ids), attribution.quality_lift,
            transfer.novel_domain_pass_rate, rollback, rationale,
            self._digest(payload),
        )


__all__ = ["LearningInterventionDecision", "EvidenceToLearningInterventionController"]
