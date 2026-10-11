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
class SealedDomain:
    domain_id: str
    case_ids: tuple[str, ...]
    novel: bool = True
    evaluation_mode: str = "zero_shot"

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
                 "evaluation_mode": domain.evaluation_mode}
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

    def __init__(self, gateway: ExternalEvaluatorGateway) -> None:
        if not callable(getattr(gateway, "evaluate", None)):
            raise TypeError("an external evaluator gateway is required")
        self.gateway = gateway

    def run(self, campaign: CrossDomainCampaign, command: ExternalEvaluatorCommand) -> SealedArenaEvidence:
        if not isinstance(campaign, CrossDomainCampaign):
            raise TypeError("a validated CrossDomainCampaign is required")
        if not isinstance(command, ExternalEvaluatorCommand):
            raise TypeError("an ExternalEvaluatorCommand is required")
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
        bound_request = replace(campaign.request, campaign_digest=campaign.campaign_digest)
        evidence = self.gateway.evaluate(bound_request, command)
        from .cross_domain_transfer_metrics import build_cross_domain_report
        return build_cross_domain_report(
            campaign, evidence, expected_request_digest=bound_request.request_digest
        )


__all__ = ["SealedDomain", "CrossDomainCampaign", "SealedCrossDomainRunner"]
