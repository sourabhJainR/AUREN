"""Evidence-chain gate joining external evaluation, run receipts, and causal attribution.

This module checks lineage consistency before a capability promotion decision can
be considered evidence-backed. It never changes runtime or lifecycle state.
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
        return self.eligible and not self.reasons


class PromotionEvidenceChainBuilder:
    """Join independent evidence without granting promotion authority."""

    def build(
        self,
        campaign: ExternalEvaluationCampaign,
        receipt: ArenaRunReceipt,
        causal: CausalPromotionEvidence,
        decision: CausalPromotionDecision,
    ) -> PromotionEvidenceChain:
        reasons = []
        if not campaign.trustworthy:
            reasons.append("external campaign is not trustworthy")
        if not receipt.trustworthy:
            reasons.append("arena run receipt is not trustworthy")
        if campaign.corpus_digest != receipt.corpus_digest:
            reasons.append("campaign and receipt corpus digests differ")
        if campaign.oracle_digest != receipt.oracle_digest:
            reasons.append("campaign and receipt oracle digests differ")
        if not causal.capability_id.strip() or not causal.intervention_id.strip():
            reasons.append("capability and intervention identities are required")
        if not decision.eligible:
            reasons.extend(decision.reasons)
        if not causal.fresh_holdout:
            reasons.append("causal evidence lacks a fresh holdout")
        if causal.contamination_detected:
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
