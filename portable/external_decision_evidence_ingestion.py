"""Ingest externally verified execution outcomes into the persistent evidence graph.

The ingestor is intentionally strict: only a complete promotion evidence chain,
an evidence-eligible campaign outcome, an independent oracle assertion, and
non-contaminated observations can become decision-steering evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math

from .external_evaluation_campaign import CampaignOutcome
from .persistent_evidence_graph import EvidenceNode, PersistentEvidenceGraph
from .promotion_evidence_chain import PromotionEvidenceChain


@dataclass(frozen=True, slots=True)
class ExternalDecisionEvidence:
    task_family: str
    capability: str
    provider: str
    tool_path: str
    success: float
    quality: float
    duration: float
    cost: float
    campaign_digest: str
    oracle_independent: bool = True
    verified: bool = True
    contaminated: bool = False
    evidence_id: str = ""

    def __post_init__(self) -> None:
        for name, value in (
            ("task_family", self.task_family),
            ("capability", self.capability),
            ("provider", self.provider),
            ("tool_path", self.tool_path),
            ("campaign_digest", self.campaign_digest),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        for name, value in (("success", self.success), ("quality", self.quality)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a finite number between 0 and 1")
        for name, value in (("duration", self.duration), ("cost", self.cost)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")
        for name in ("oracle_independent", "verified", "contaminated"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")

        payload = {
            "task_family": self.task_family, "capability": self.capability,
            "provider": self.provider, "tool_path": self.tool_path,
            "success": self.success, "quality": self.quality,
            "duration": self.duration, "cost": self.cost,
            "campaign_digest": self.campaign_digest,
            "oracle_independent": self.oracle_independent,
            "verified": self.verified,
            "contaminated": self.contaminated,
        }
        expected_id = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        if not self.evidence_id:
            object.__setattr__(self, "evidence_id", expected_id)
        elif not isinstance(self.evidence_id, str) or self.evidence_id != expected_id:
            raise ValueError("evidence_id does not match canonical observation content")

@dataclass(frozen=True, slots=True)
class EvidenceIngestionResult:
    evidence_id: str
    capability_node_id: str
    observation_node_id: str
    campaign_node_id: str
    chain_node_id: str
    ingested: bool


class ExternalDecisionEvidenceIngestor:
    """Append only externally verified decision observations."""

    def __init__(self, graph: PersistentEvidenceGraph) -> None:
        if not isinstance(graph, PersistentEvidenceGraph):
            raise TypeError("graph must be PersistentEvidenceGraph")
        self.graph = graph

    def ingest(
        self,
        chain: PromotionEvidenceChain,
        outcome: CampaignOutcome,
        evidence: ExternalDecisionEvidence,
    ) -> EvidenceIngestionResult:
        if not chain.complete:
            raise ValueError("promotion evidence chain is incomplete")
        if outcome.campaign_digest != chain.campaign_digest:
            raise ValueError("campaign outcome does not match evidence chain")
        if not outcome.evidence_eligible:
            raise ValueError("campaign outcome is not evidence eligible")
        if evidence.campaign_digest != chain.campaign_digest:
            raise ValueError("decision evidence does not match campaign")
        if evidence.evidence_id not in chain.causal_evidence_ids:
            raise ValueError("decision observation is not bound to the causal evidence chain")
        if not evidence.verified or not evidence.oracle_independent:
            raise ValueError("decision evidence requires independent verification")
        if evidence.contaminated:
            raise ValueError("contaminated decision evidence cannot be ingested")

        capability = self.graph.add_node(
            "capability", evidence.capability,
            {"source": "external-evaluation", "chain_digest": chain.causal_evidence_digest},
        )
        campaign = self.graph.add_node(
            "campaign", chain.campaign_digest,
            {"campaign_digest": chain.campaign_digest},
        )
        observation = self.graph.add_node(
            "observation", evidence.evidence_id,
            {
                "kind": "decision-observation",
                "task_family": evidence.task_family,
                "capability": evidence.capability,
                "provider": evidence.provider,
                "tool_path": evidence.tool_path,
                "success": str(float(evidence.success)),
                "quality": str(float(evidence.quality)),
                "duration": str(float(evidence.duration)),
                "cost": str(float(evidence.cost)),
                "verified": "true",
                "contaminated": "false",
                "oracle_independent": "true",
                "observed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        chain_node = self.graph.add_node(
            "promotion-evidence-chain", chain.causal_evidence_digest,
            {"campaign_digest": chain.campaign_digest, "capability": chain.capability_id},
        )
        self.graph.add_edge(observation, "supports", capability)
        self.graph.add_edge(campaign, "produced", observation)
        self.graph.add_edge(chain_node, "attests", campaign)
        return EvidenceIngestionResult(
            evidence.evidence_id, capability.node_id, observation.node_id,
            campaign.node_id, chain_node.node_id, True,
        )


__all__ = [
    "ExternalDecisionEvidence",
    "EvidenceIngestionResult",
    "ExternalDecisionEvidenceIngestor",
]
