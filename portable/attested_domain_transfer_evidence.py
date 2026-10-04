"""Build auditable cross-domain transfer observations from sealed outcomes.

This adapter joins only externally attested outcome rows to domain ownership and
never infers success for missing cases. It is analysis/evidence plumbing, not
capability promotion.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .attestation_freshness import FreshEvaluationAttestation, FreshnessAwareAttestor
from .sealed_cross_domain_arena import CrossDomainCampaign
from .attested_cross_project_transfer import (
    AttestedCrossProjectTransferEvaluator,
    CrossProjectTransferAssessment,
    ProjectTransferObservation,
)


@dataclass(frozen=True, slots=True)
class DomainTransferEvidence:
    observations: tuple[ProjectTransferObservation, ...]
    assessment: CrossProjectTransferAssessment


class AttestedDomainTransferEvidenceBuilder:
    """Convert externally verified case outcomes into domain-scoped transfer evidence."""

    def __init__(self, evaluator: AttestedCrossProjectTransferEvaluator | None = None) -> None:
        self.evaluator = evaluator or AttestedCrossProjectTransferEvaluator()

    def build(
        self,
        campaign: CrossDomainCampaign,
        *,
        results: Mapping[str, tuple[bool, bool, str]],
        attestations: Sequence,
        source_project: str,
        target_project: str,
        capability: str,
        min_samples: int = 5,
        min_transfer_rate: float = 0.8,
    ) -> DomainTransferEvidence:
        if not source_project.strip() or not target_project.strip():
            raise ValueError("source and target projects are required")
        if source_project == target_project:
            raise ValueError("source and target projects must differ")
        domain_by_case = {
            case_id: domain.domain_id
            for domain in campaign.domains
            for case_id in domain.case_ids
        }
        if not domain_by_case:
            raise ValueError("campaign has no domain cases")
        attested_ids = {a.evidence_digest for a in attestations if a.trustworthy}
        observations: list[ProjectTransferObservation] = []
        for case_id, (success, regression, evidence_digest) in results.items():
            if case_id not in domain_by_case:
                raise ValueError(f"result references unknown domain case: {case_id}")
            if evidence_digest not in attested_ids:
                raise ValueError(f"result lacks trustworthy independent attestation: {case_id}")
            observations.append(
                ProjectTransferObservation(
                    source_project=source_project,
                    target_project=target_project,
                    capability=capability,
                    success=success,
                    regression=regression,
                    holdout=True,
                    independent_oracle=True,
                    attestation_digest=evidence_digest,
                )
            )
        domains_observed = {domain_by_case[o.attestation_digest] for o in ()}
        if len(observations) < min_samples:
            raise ValueError("insufficient attested transfer observations")
        assessment = self.evaluator.evaluate(
            observations,
            attestations,
            min_samples=min_samples,
            min_transfer_rate=min_transfer_rate,
        )
        return DomainTransferEvidence(tuple(observations), assessment)


__all__ = ["DomainTransferEvidence", "AttestedDomainTransferEvidenceBuilder"]
