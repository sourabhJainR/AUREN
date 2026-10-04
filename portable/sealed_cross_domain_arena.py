"""Sealed, domain-agnostic Arena campaign contract.

This phase makes genuine cross-domain evaluation executable at the protocol
boundary: each domain is independently identified, each domain must contribute
holdouts, and the benchmark oracle remains outside AUREN.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Sequence

from .sealed_arena_boundary import SealedCampaignRequest
from .external_evaluator_gateway import ExternalEvaluatorCommand, ExternalEvaluatorGateway
from .sealed_arena_boundary import SealedArenaEvidence


@dataclass(frozen=True, slots=True)
class SealedDomain:
    domain_id: str
    case_ids: tuple[str, ...]
    novel: bool = True

    def __post_init__(self) -> None:
        if not self.domain_id.strip() or not self.case_ids:
            raise ValueError("domain_id and case_ids are required")
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("domain contains duplicate case ids")


@dataclass(frozen=True, slots=True)
class CrossDomainCampaign:
    campaign_id: str
    request: SealedCampaignRequest
    domains: tuple[SealedDomain, ...]
    independent_oracle_id: str
    evaluator_id: str

    def __post_init__(self) -> None:
        if not self.campaign_id.strip() or not self.independent_oracle_id.strip() or not self.evaluator_id.strip():
            raise ValueError("campaign, evaluator, and oracle ids are required")
        if self.evaluator_id == self.independent_oracle_id:
            raise ValueError("evaluator and oracle must be distinct")
        if len(self.domains) < 2:
            raise ValueError("cross-domain campaign requires at least two domains")
        request_ids = {case.case_id for case in self.request.cases}
        domain_ids = [domain.domain_id for domain in self.domains]
        if len(domain_ids) != len(set(domain_ids)):
            raise ValueError("cross-domain campaign contains duplicate domains")
        assigned = {case_id for domain in self.domains for case_id in domain.case_ids}
        if not assigned <= request_ids:
            raise ValueError("domain references a case outside the sealed request")
        if len(assigned) != sum(len(domain.case_ids) for domain in self.domains):
            raise ValueError("a case may belong to only one domain")
        holdouts = {case.case_id for case in self.request.cases if case.holdout}
        if any(not (set(domain.case_ids) & holdouts) for domain in self.domains):
            raise ValueError("every domain requires at least one holdout case")

    @property
    def campaign_digest(self) -> str:
        payload = {
            "campaign_id": self.campaign_id,
            "request_digest": hashlib.sha256(
                json.dumps(
                    {
                        "campaign_digest": self.request.campaign_digest,
                        "corpus_digest": self.request.corpus_digest,
                        "cases": [case.as_dict() for case in self.request.cases],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "domains": [
                {"domain_id": d.domain_id, "case_ids": d.case_ids, "novel": d.novel}
                for d in self.domains
            ],
            "evaluator_id": self.evaluator_id,
            "independent_oracle_id": self.independent_oracle_id,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class SealedCrossDomainRunner:
    """Run an externally supplied evaluator against a sealed multi-domain campaign."""

    def __init__(self, gateway: ExternalEvaluatorGateway) -> None:
        self.gateway = gateway

    def run(self, campaign: CrossDomainCampaign, command: ExternalEvaluatorCommand) -> SealedArenaEvidence:
        return self.gateway.evaluate(campaign.request, command)


__all__ = ["SealedDomain", "CrossDomainCampaign", "SealedCrossDomainRunner"]
