"""Evidence-chain gate joining verified campaign attestation, run receipts, and causal attribution.

The gate checks exact campaign/receipt lineage and requires the causal decision
to reference the exact causal evidence and run receipt. It never mutates state.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
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
        trusted_isolation_signers: frozenset[str] | None = None,
        isolation_now: float | None = None,
        attestation_now: float | None = None,
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
                    trusted_signer_ids=trusted_isolation_signers,
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
        now = time.time() if attestation_now is None else attestation_now
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now) or now < 0:
            reasons.append("attestation verification time is invalid")
        if not attestation.run_id or attestation.run_id != receipt.run_id:
            reasons.append("attestation run id does not match receipt")
        if not attestation.receipt_digest or attestation.receipt_digest != receipt.receipt_digest:
            reasons.append("attestation receipt digest does not match receipt")
        if not attestation.runtime_digest or attestation.runtime_digest != receipt.runtime_snapshot:
            reasons.append("attestation runtime digest does not match receipt")
        if not attestation.issued_at_epoch or not attestation.expires_at_epoch:
            reasons.append("attestation freshness timestamps are required")
        elif isinstance(now, (int, float)) and not isinstance(now, bool) and math.isfinite(now):
            if attestation.issued_at_epoch > now:
                reasons.append("attestation was issued in the future")
            if attestation.expires_at_epoch <= now:
                reasons.append("attestation has expired")
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


@dataclass(frozen=True, slots=True)
class ArenaEvidenceObservation:
    """Immutable accepted or rejected result of one evidence-chain evaluation."""

    campaign_digest: str
    run_receipt_digest: str
    causal_evidence_digest: str
    capability_id: str
    intervention_id: str
    accepted: bool
    reasons: tuple[str, ...]
    chain_digest: str
    observation_digest: str = ""

    def __post_init__(self) -> None:
        for name in (
            "campaign_digest", "run_receipt_digest", "causal_evidence_digest",
            "capability_id", "intervention_id", "chain_digest",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if type(self.accepted) is not bool:
            raise ValueError("accepted must be a boolean")
        if not isinstance(self.reasons, tuple) or any(not isinstance(x, str) or not x.strip() for x in self.reasons):
            raise ValueError("reasons must be a tuple of non-empty strings")
        if self.accepted and self.reasons:
            raise ValueError("accepted evidence cannot contain rejection reasons")
        if not self.accepted and not self.reasons:
            raise ValueError("rejected evidence must preserve at least one reason")
        expected = hashlib.sha256(json.dumps({
            "campaign_digest": self.campaign_digest,
            "run_receipt_digest": self.run_receipt_digest,
            "causal_evidence_digest": self.causal_evidence_digest,
            "capability_id": self.capability_id,
            "intervention_id": self.intervention_id,
            "accepted": self.accepted,
            "reasons": self.reasons,
            "chain_digest": self.chain_digest,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if self.observation_digest and self.observation_digest != expected:
            raise ValueError("observation_digest does not match observation contents")
        object.__setattr__(self, "observation_digest", expected)

    @classmethod
    def from_chain(cls, chain: PromotionEvidenceChain) -> "ArenaEvidenceObservation":
        if not isinstance(chain, PromotionEvidenceChain):
            raise TypeError("PromotionEvidenceChain is required")
        chain_digest = hashlib.sha256(json.dumps({
            "campaign_digest": chain.campaign_digest,
            "run_receipt_digest": chain.run_receipt_digest,
            "causal_evidence_digest": chain.causal_evidence_digest,
            "capability_id": chain.capability_id,
            "intervention_id": chain.intervention_id,
            "eligible": chain.eligible,
            "reasons": chain.reasons,
            "attestation_evidence_digest": chain.attestation_evidence_digest,
            "causal_evidence_ids": chain.causal_evidence_ids,
            "isolation_evidence_digest": chain.isolation_evidence_digest,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        accepted = chain.complete
        reasons = () if accepted else (chain.reasons if chain.reasons else ("evidence chain is incomplete",))
        return cls(
            chain.campaign_digest, chain.run_receipt_digest, chain.causal_evidence_digest,
            chain.capability_id, chain.intervention_id, accepted, tuple(reasons), chain_digest,
        )


class ArenaEvidenceObservationLedger:
    """Append-only in-memory ledger; only accepted observations are learning inputs.

    Persistence and trust-root custody belong to the deployment's external
    evidence store. This class prevents accidental duplicate/conflicting
    receipt ingestion within the current ledger instance.
    """

    def __init__(self) -> None:
        self._by_receipt: dict[str, ArenaEvidenceObservation] = {}
        self._records: list[ArenaEvidenceObservation] = []

    def record(self, chain: PromotionEvidenceChain) -> ArenaEvidenceObservation:
        observation = ArenaEvidenceObservation.from_chain(chain)
        prior = self._by_receipt.get(observation.run_receipt_digest)
        if prior is not None:
            if prior.observation_digest == observation.observation_digest:
                raise ValueError("replayed Arena receipt evidence")
            raise ValueError("conflicting Arena observations for receipt digest")
        self._by_receipt[observation.run_receipt_digest] = observation
        self._records.append(observation)
        return observation

    @property
    def records(self) -> tuple[ArenaEvidenceObservation, ...]:
        return tuple(self._records)

    @property
    def learning_inputs(self) -> tuple[ArenaEvidenceObservation, ...]:
        return tuple(record for record in self._records if record.accepted)


__all__ = ["PromotionEvidenceChain", "PromotionEvidenceChainBuilder", "ArenaEvidenceObservation", "ArenaEvidenceObservationLedger"]
