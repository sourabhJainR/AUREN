"""Evidence-chain gate joining external evaluation, run receipts, and causal attribution.

The gate checks campaign/receipt lineage and requires the causal decision to
reference the exact causal evidence and run receipt. It never mutates state.
"""
from __future__ import annotations

from dataclasses import dataclass

from .arena_run_receipt import ArenaRunReceipt
from .causal_capability_promotion import CausalPromotionDecision, CausalPromotionEvidence
from .external_evaluation_campaign import ExternalEvaluationCampaign


@dataclass(frozen=True, slots=True)
class PromotionEvidenceChain:
    campaign_digest: str
    run_receipt_digest: str
    causal_evidence_digest: str
    capability_id: str
    intervention_id: str
    eligible: bool
    reasons: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.eligible is True and not self.reasons


class PromotionEvidenceChainBuilder:
    """Join independent evidence without granting promotion authority."""

    def build(
        self,
        campaign: ExternalEvaluationCampaign,
        receipt: ArenaRunReceipt,
        causal: CausalPromotionEvidence,
        decision: CausalPromotionDecision,
    ) -> PromotionEvidenceChain:
        if not isinstance(campaign, ExternalEvaluationCampaign):
            raise TypeError("ExternalEvaluationCampaign is required")
        if not isinstance(receipt, ArenaRunReceipt):
            raise TypeError("ArenaRunReceipt is required")
        if not isinstance(causal, CausalPromotionEvidence):
            raise TypeError("CausalPromotionEvidence is required")
        if not isinstance(decision, CausalPromotionDecision):
            raise TypeError("CausalPromotionDecision is required")

        reasons: list[str] = []
        if not campaign.trustworthy:
            reasons.append("external campaign is not trustworthy")
        if not receipt.trustworthy:
            reasons.append("arena run receipt is not trustworthy")
        if receipt.campaign_digest != campaign.campaign_digest:
            reasons.append("campaign and receipt campaign digests differ")
        if campaign.corpus_digest != receipt.corpus_digest:
            reasons.append("campaign and receipt corpus digests differ")
        if campaign.oracle_digest != receipt.oracle_digest:
            reasons.append("campaign and receipt oracle digests differ")
        if campaign.evaluator_version != receipt.evaluator_version:
            reasons.append("campaign and receipt evaluator versions differ")
        if campaign.runtime_snapshot != receipt.runtime_snapshot:
            reasons.append("campaign and receipt runtime snapshots differ")
        if set(campaign.case_ids) != set(receipt.case_ids):
            reasons.append("campaign and receipt case sets differ")
        if set(campaign.holdout_case_ids) != set(receipt.holdout_case_ids):
            reasons.append("campaign and receipt holdout sets differ")
        if receipt.receipt_digest not in causal.evidence_ids:
            reasons.append("causal evidence does not reference this run receipt digest")
        if decision.evidence_digest != causal.evidence_digest:
            reasons.append("promotion decision does not reference this causal evidence digest")
        if not decision.eligible:
            reasons.extend(decision.reasons)
        if causal.fresh_holdout is not True:
            reasons.append("causal evidence lacks a fresh holdout")
        if causal.independent_oracle is not True:
            reasons.append("causal evidence lacks an independent oracle")
        if causal.contamination_detected is not False:
            reasons.append("causal evidence reports contamination")
        return PromotionEvidenceChain(
            campaign.campaign_digest,
            receipt.receipt_digest,
            causal.evidence_digest,
            causal.capability_id,
            causal.intervention_id,
            not reasons,
            tuple(dict.fromkeys(reasons)),
        )


__all__ = ["PromotionEvidenceChain", "PromotionEvidenceChainBuilder"]
