"""Evidence-chain gate joining verified campaign attestation, run receipts, and causal attribution.

The gate checks exact campaign/receipt lineage and requires the causal decision
to reference the exact causal evidence and run receipt. It never mutates state.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from .arena_run_receipt import ArenaRunReceipt
from .arena_isolation_attestation import ArenaIsolationAttestation, verify_arena_isolation_attestation
from .causal_capability_promotion import CausalPromotionDecision, CausalPromotionEvidence
from .external_evaluation_campaign import ExternalEvaluationCampaign
from .independent_evaluation_attestation import AttestedEvaluation


@dataclass(frozen=True, slots=True)
class PromotionEvidenceChain:
    # Keep the original positional field order for compatibility with existing
    # consumers; the verified attestation digest is an additional required
    # structural condition for a chain to be complete.
    campaign_digest: str
    run_receipt_digest: str
    causal_evidence_digest: str
    capability_id: str
    intervention_id: str
    eligible: bool
    reasons: tuple[str, ...]
    attestation_evidence_digest: str = ""
    causal_evidence_ids: tuple[str, ...] = ()
    isolation_evidence_digest: str = ""

    def __post_init__(self) -> None:
        for name, value in (
            ("campaign_digest", self.campaign_digest),
            ("run_receipt_digest", self.run_receipt_digest),
            ("causal_evidence_digest", self.causal_evidence_digest),
            ("capability_id", self.capability_id),
            ("intervention_id", self.intervention_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if type(self.eligible) is not bool:
            raise ValueError("eligible must be a boolean")
        if not isinstance(self.reasons, tuple) or any(not isinstance(reason, str) or not reason.strip() for reason in self.reasons):
            raise ValueError("reasons must be a tuple of non-empty strings")
        if not isinstance(self.attestation_evidence_digest, str):
            raise ValueError("attestation_evidence_digest must be a string")
        if not isinstance(self.isolation_evidence_digest, str):
            raise ValueError("isolation_evidence_digest must be a string")
        if not isinstance(self.causal_evidence_ids, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.causal_evidence_ids
        ):
            raise ValueError("causal_evidence_ids must be a tuple of non-empty strings")
        if len(set(self.causal_evidence_ids)) != len(self.causal_evidence_ids):
            raise ValueError("causal_evidence_ids must be unique")

    @property
    def complete(self) -> bool:
        return (
            self.eligible is True
            and not self.reasons
            and bool(self.attestation_evidence_digest.strip())
            and bool(self.causal_evidence_ids)
            and bool(self.isolation_evidence_digest.strip())
        )


class PromotionEvidenceChainBuilder:
    """Join independently verified evidence without granting promotion authority."""

    def build(
        self,
        campaign: ExternalEvaluationCampaign,
        receipt: ArenaRunReceipt,
        causal: CausalPromotionEvidence,
        decision: CausalPromotionDecision,
        *,
        attested_campaign: AttestedEvaluation,
        isolation_attestation: ArenaIsolationAttestation | None = None,
        verify_isolation_signature: Callable[[str, str, bytes], bool] | None = None,
        expected_image_digest: str = "",
        evaluator_id: str = "",
        oracle_id: str = "",
        policy_runtime_id: str = "",
        isolation_now: float | None = None,
    ) -> PromotionEvidenceChain:
        if not isinstance(campaign, ExternalEvaluationCampaign):
            raise TypeError("ExternalEvaluationCampaign is required")
        if not isinstance(receipt, ArenaRunReceipt):
            raise TypeError("ArenaRunReceipt is required")
        if not isinstance(causal, CausalPromotionEvidence):
            raise TypeError("CausalPromotionEvidence is required")
        if not isinstance(decision, CausalPromotionDecision):
            raise TypeError("CausalPromotionDecision is required")
        if not isinstance(attested_campaign, AttestedEvaluation):
            raise TypeError("verified campaign attestation is required")

        reasons: list[str] = []
        isolation_digest = ""
        if isolation_attestation is None:
            reasons.append("independently signed sandbox isolation attestation is required")
        else:
            try:
                isolation_digest = verify_arena_isolation_attestation(
                    isolation_attestation,
                    verify_isolation_signature,
                    expected_campaign_digest=campaign.campaign_digest,
                    expected_run_receipt_digest=receipt.receipt_digest,
                    expected_image_digest=expected_image_digest,
                    evaluator_id=evaluator_id,
                    oracle_id=oracle_id,
                    policy_runtime_id=policy_runtime_id,
                    now=time.time() if isolation_now is None else isolation_now,
                )
            except (TypeError, ValueError) as exc:
                reasons.append("sandbox isolation attestation invalid: " + str(exc))
        if not campaign.structurally_valid:
            reasons.append("external campaign is structurally invalid or contaminated")
        if not campaign.has_attestation_material:
            reasons.append("external campaign has no attestation material")
        if not attested_campaign.trustworthy:
            reasons.append("external campaign attestation is not independently verified")
        attestation = attested_campaign.attestation
        if attestation.campaign_digest != campaign.campaign_digest:
            reasons.append("attestation and campaign digests differ")
        if attestation.corpus_digest != campaign.corpus_digest:
            reasons.append("attestation and campaign corpus digests differ")
        if attestation.oracle_digest != campaign.oracle_digest:
            reasons.append("attestation and campaign oracle digests differ")
        if attestation.evaluator_version != campaign.evaluator_version:
            reasons.append("attestation and campaign evaluator versions differ")
        if attestation.signature != campaign.external_attestation:
            reasons.append("attestation signature does not match campaign attestation material")
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
        if decision.eligible is not True:
            reasons.extend(decision.reasons)
        if causal.fresh_holdout is not True:
            reasons.append("causal evidence lacks a fresh holdout")
        if causal.independent_oracle is not True:
            reasons.append("causal evidence lacks an independent oracle")
        if causal.contamination_detected is not False:
            reasons.append("causal evidence reports contamination")
        return PromotionEvidenceChain(
            campaign_digest=campaign.campaign_digest,
            run_receipt_digest=receipt.receipt_digest,
            causal_evidence_digest=causal.evidence_digest,
            capability_id=causal.capability_id,
            intervention_id=causal.intervention_id,
            eligible=not reasons,
            reasons=tuple(dict.fromkeys(reasons)),
            attestation_evidence_digest=attested_campaign.evidence_digest,
            causal_evidence_ids=causal.evidence_ids,
            isolation_evidence_digest=isolation_digest,
        )


__all__ = ["PromotionEvidenceChain", "PromotionEvidenceChainBuilder"]
