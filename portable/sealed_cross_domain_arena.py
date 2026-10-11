"""Sealed, domain-agnostic Arena campaign contract.

Each domain is independently identified and included in the digest sent to the
external evaluator. The benchmark oracle remains outside AUREN.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json

from .sealed_arena_boundary import SealedArenaEvidence, SealedCampaignRequest
from .external_evaluator_gateway import ExternalEvaluatorCommand, ExternalEvaluatorGateway


def _require_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class TargetDomainProvenance:
    """Target-domain lineage; digest is not a substitute for signature verification."""
    domain_id: str
    origin: str
    manifest_digest: str
    split: str
    development_corpus_digest: str
    development_overlap_status: str
    overlap_attestation_digest: str
    independent_operator_id: str
    attestation_digest: str
    attestation_signer_id: str = ""
    attestation_signature: str = ""

    def __post_init__(self) -> None:
        for name, value in (("domain_id", self.domain_id), ("origin", self.origin), ("manifest_digest", self.manifest_digest), ("split", self.split), ("development_corpus_digest", self.development_corpus_digest), ("development_overlap_status", self.development_overlap_status), ("overlap_attestation_digest", self.overlap_attestation_digest), ("independent_operator_id", self.independent_operator_id), ("attestation_digest", self.attestation_digest)):
            _require_text(name, value)
        if self.split != "sealed_holdout":
            raise ValueError("target-domain provenance must use sealed_holdout split")
        if self.development_overlap_status != "no_overlap":
            raise ValueError("target domain must have attested no-overlap with development")
        if self.independent_operator_id.lower() in {"unknown", "none"}:
            raise ValueError("target-domain provenance requires an independent operator")

    @property
    def provenance_digest(self) -> str:
        payload = {
            "domain_id": self.domain_id,
            "origin": self.origin,
            "manifest_digest": self.manifest_digest,
            "split": self.split,
            "development_corpus_digest": self.development_corpus_digest,
            "development_overlap_status": self.development_overlap_status,
            "overlap_attestation_digest": self.overlap_attestation_digest,
            "independent_operator_id": self.independent_operator_id,
            "attestation_digest": self.attestation_digest,
            "attestation_signer_id": self.attestation_signer_id,
            "attestation_signature": self.attestation_signature,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SealedDomain:
    domain_id: str
    case_ids: tuple[str, ...]
    novel: bool = True
    evaluation_mode: str = "zero_shot"
    provenance: TargetDomainProvenance | None = None

    def __post_init__(self) -> None:
        _require_text("domain_id", self.domain_id)
        if not isinstance(self.case_ids, tuple) or not self.case_ids:
            raise ValueError("case_ids must be a non-empty tuple")
        if any(not isinstance(case_id, str) or not case_id.strip() for case_id in self.case_ids):
            raise ValueError("case_ids must contain non-empty strings")
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("domain contains duplicate case ids")
        if type(self.novel) is not bool:
            raise ValueError("novel must be a boolean")
        if self.evaluation_mode not in {"zero_shot", "few_shot", "adapted"}:
            raise ValueError("evaluation_mode must be zero_shot, few_shot, or adapted")
        if self.provenance is not None:
            if not isinstance(self.provenance, TargetDomainProvenance):
                raise ValueError("provenance must be a TargetDomainProvenance value")
            if self.provenance.domain_id != self.domain_id:
                raise ValueError("target-domain provenance identity does not match domain_id")


@dataclass(frozen=True, slots=True)
class CrossDomainCampaign:
    campaign_id: str
    request: SealedCampaignRequest
    domains: tuple[SealedDomain, ...]
    independent_oracle_id: str
    evaluator_id: str
    capability_id: str = ""

    def __post_init__(self) -> None:
        _require_text("campaign_id", self.campaign_id)
        _require_text("independent_oracle_id", self.independent_oracle_id)
        _require_text("evaluator_id", self.evaluator_id)
        if not isinstance(self.capability_id, str):
            raise ValueError("capability_id must be a string")
        if not isinstance(self.request, SealedCampaignRequest):
            raise ValueError("cross-domain campaign requires a sealed campaign request")
        if self.evaluator_id == self.independent_oracle_id:
            raise ValueError("evaluator and oracle must be distinct")
        if not isinstance(self.domains, tuple) or len(self.domains) < 2:
            raise ValueError("cross-domain campaign requires at least two domains")
        if any(not isinstance(domain, SealedDomain) for domain in self.domains):
            raise ValueError("domains must contain SealedDomain values")
        request_ids = {case.case_id for case in self.request.cases}
        domain_ids = [domain.domain_id for domain in self.domains]
        if len(domain_ids) != len(set(domain_ids)):
            raise ValueError("cross-domain campaign contains duplicate domains")
        assigned = {case_id for domain in self.domains for case_id in domain.case_ids}
        if not assigned <= request_ids:
            raise ValueError("domain references a case outside the sealed request")
        if len(assigned) != sum(len(domain.case_ids) for domain in self.domains):
            raise ValueError("a case may belong to only one domain")
        if not any(domain.novel is True for domain in self.domains):
            raise ValueError("cross-domain campaign requires at least one explicitly novel domain")
        holdouts = {case.case_id for case in self.request.cases if case.holdout is True}
        if any(not (set(domain.case_ids) & holdouts) for domain in self.domains):
            raise ValueError("every domain requires at least one holdout case")
        if assigned != request_ids:
            raise ValueError("every sealed case must be assigned to exactly one domain")

    @property
    def campaign_digest(self) -> str:
        payload = {
            "campaign_id": self.campaign_id,
            "request_digest": self.request.request_digest,
            "domains": [
                {"domain_id": domain.domain_id, "case_ids": domain.case_ids, "novel": domain.novel,
                 "evaluation_mode": domain.evaluation_mode, "provenance_digest": domain.provenance.provenance_digest if domain.provenance else None}
                for domain in self.domains
            ],
            "capability_id": self.capability_id,
            "evaluator_id": self.evaluator_id,
            "independent_oracle_id": self.independent_oracle_id,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class SealedCrossDomainRunner:
    """Run an external evaluator against a digest-bound multi-domain campaign."""

    def __init__(
        self,
        gateway: ExternalEvaluatorGateway,
        *,
        target_provenance_verifier=None,
        trusted_target_operator_ids: frozenset[str] | None = None,
    ) -> None:
        if not callable(getattr(gateway, "evaluate", None)):
            raise TypeError("an external evaluator gateway is required")
        if target_provenance_verifier is not None and not callable(target_provenance_verifier):
            raise TypeError("target_provenance_verifier must be callable")
        if trusted_target_operator_ids is not None and (
            not isinstance(trusted_target_operator_ids, frozenset)
            or not trusted_target_operator_ids
            or any(not isinstance(item, str) or not item.strip() for item in trusted_target_operator_ids)
        ):
            raise ValueError("trusted_target_operator_ids must be a non-empty frozenset of operator IDs")
        self.gateway = gateway
        self.target_provenance_verifier = target_provenance_verifier
        self.trusted_target_operator_ids = trusted_target_operator_ids

    def _verify_target_provenance(self, campaign: CrossDomainCampaign) -> None:
        targets = [domain for domain in campaign.domains if domain.novel is True]
        if not targets:
            raise ValueError("at least one target domain is required")
        if self.target_provenance_verifier is None:
            raise ValueError("independent target-domain provenance verifier is required")
        if not self.trusted_target_operator_ids:
            raise ValueError("out-of-band trusted target-domain operator allowlist is required")
        for domain in targets:
            provenance = domain.provenance
            if provenance is None:
                raise ValueError(f"target domain {domain.domain_id} is missing held-out provenance")
            if provenance.manifest_digest != campaign.request.manifest_digest:
                raise ValueError(f"target domain {domain.domain_id} provenance manifest mismatch")
            if provenance.development_corpus_digest == campaign.request.corpus_digest:
                raise ValueError(f"target domain {domain.domain_id} reuses the development corpus")
            if provenance.independent_operator_id in {campaign.evaluator_id, campaign.independent_oracle_id}:
                raise ValueError(f"target domain {domain.domain_id} operator must be independent of evaluator and oracle")
            if provenance.attestation_signer_id != provenance.independent_operator_id or not provenance.attestation_signature:
                raise ValueError(f"target domain {domain.domain_id} is missing a matching operator signature")
            if provenance.independent_operator_id not in self.trusted_target_operator_ids:
                raise ValueError(f"target domain {domain.domain_id} operator is not in the out-of-band trusted allowlist")
            if self.target_provenance_verifier(provenance) is not True:
                raise ValueError(f"target domain {domain.domain_id} provenance attestation is not verified")

    def run(self, campaign: CrossDomainCampaign, command: ExternalEvaluatorCommand) -> SealedArenaEvidence:
        if not isinstance(campaign, CrossDomainCampaign):
            raise TypeError("a validated CrossDomainCampaign is required")
        if not isinstance(command, ExternalEvaluatorCommand):
            raise TypeError("an ExternalEvaluatorCommand is required")
        self._verify_target_provenance(campaign)
        # The previous implementation validated domain assignments locally but
        # sent only request.campaign_digest to the external evaluator. A receipt
        # could therefore be valid while not attesting to the domain assignment
        # used by the transfer scorer. Bind the full cross-domain campaign digest
        # into the exact request the gateway verifies against the signed receipt.
        bound_request = replace(campaign.request, campaign_digest=campaign.campaign_digest)
        return self.gateway.evaluate(bound_request, command)

    def run_with_report(self, campaign: CrossDomainCampaign, command: ExternalEvaluatorCommand):
        """Run and produce capability-specific, mode-separated transfer metrics.

        Every holdout must carry signed per-case metrics. Missing metrics fail
        closed rather than being silently treated as zero or inferred.
        """
        if not isinstance(campaign, CrossDomainCampaign):
            raise TypeError("a validated CrossDomainCampaign is required")
        if not isinstance(command, ExternalEvaluatorCommand):
            raise TypeError("an ExternalEvaluatorCommand is required")
        if not campaign.capability_id.strip():
            raise ValueError("capability_id is required for cross-domain transfer reporting")
        self._verify_target_provenance(campaign)
        bound_request = replace(campaign.request, campaign_digest=campaign.campaign_digest)
        evidence = self.gateway.evaluate(bound_request, command)
        from .cross_domain_transfer_metrics import build_cross_domain_report
        return build_cross_domain_report(
            campaign, evidence, expected_request_digest=bound_request.request_digest
        )


__all__ = ["TargetDomainProvenance", "SealedDomain", "CrossDomainCampaign", "SealedCrossDomainRunner"]
