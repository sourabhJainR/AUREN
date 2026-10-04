"""End-to-end coordinator for externally evaluated cross-domain evidence.

The coordinator composes existing sealed execution, attestation freshness, and
transfer-evidence layers. It never sees benchmark answers and cannot promote a
capability.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .attestation_freshness import FreshEvaluationAttestation, FreshnessAwareAttestor
from .attestation_key_separation import AttestationTrustPolicy
from .attested_domain_transfer_evidence import DomainTransferEvidence, AttestedDomainTransferEvidenceBuilder
from .external_evaluator_gateway import ExternalEvaluatorCommand, ExternalEvaluatorGateway
from .sealed_cross_domain_arena import CrossDomainCampaign


@dataclass(frozen=True, slots=True)
class ExternalTransferEvidence:
    campaign_digest: str
    execution_evidence: object
    transfer_evidence: DomainTransferEvidence


class ExternalTransferEvidenceCoordinator:
    """Compose independent execution and transfer evidence without lifecycle authority."""

    def __init__(
        self,
        gateway: ExternalEvaluatorGateway,
        *,
        verify_signature,
        attestation_policy: AttestationTrustPolicy,
        freshness_policy,
        transfer_builder: AttestedDomainTransferEvidenceBuilder | None = None,
    ) -> None:
        self.gateway = gateway
        self.attestor = FreshnessAwareAttestor(
            verify_signature, attestation_policy, freshness_policy
        )
        self.transfer_builder = transfer_builder or AttestedDomainTransferEvidenceBuilder()

    def execute_and_attribute(
        self,
        campaign: CrossDomainCampaign,
        command: ExternalEvaluatorCommand,
        *,
        attestation: FreshEvaluationAttestation,
        now: int,
        results: Mapping[str, tuple[bool, bool, str]],
        source_project: str,
        target_project: str,
        capability: str,
        min_samples: int = 5,
        min_transfer_rate: float = 0.8,
    ) -> ExternalTransferEvidence:
        execution = self.gateway.evaluate(campaign.request, command)
        verified = self.attestor.verify(
            attestation,
            evaluator_id=campaign.evaluator_id,
            oracle_id=campaign.independent_oracle_id,
            expected_campaign_digest=campaign.request.campaign_digest,
            expected_corpus_digest=campaign.request.corpus_digest,
            expected_oracle_digest=attestation.attestation.oracle_digest,
            now=now,
        )
        if not verified.trustworthy:
            raise ValueError(f"external campaign attestation is not trustworthy: {verified.reasons}")
        transfer = self.transfer_builder.build(
            campaign,
            results=results,
            attestations=(verified,),
            source_project=source_project,
            target_project=target_project,
            capability=capability,
            min_samples=min_samples,
            min_transfer_rate=min_transfer_rate,
        )
        return ExternalTransferEvidence(campaign.campaign_digest, execution, transfer)


__all__ = ["ExternalTransferEvidence", "ExternalTransferEvidenceCoordinator"]
