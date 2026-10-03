"""Bounded open-ended capability-gap discovery.

Discovers candidate capabilities from observed failures, transfer gaps, and
novel environment demands. It produces invention proposals only; execution,
promotion, and lifecycle mutation remain outside this module.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CapabilityGap:
    gap_id: str
    source_evidence: tuple[str, ...]
    domains: tuple[str, ...]
    failed_conditions: tuple[str, ...]
    severity: float
    transfer_gap: float

    def __post_init__(self):
        if not self.gap_id.strip() or not self.source_evidence:
            raise ValueError("gap identity and evidence are required")
        if not self.domains:
            raise ValueError("at least one domain is required")
        if not 0 <= self.severity <= 1 or not 0 <= self.transfer_gap <= 1:
            raise ValueError("gap metrics must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class CapabilityInventionProposal:
    proposal_id: str
    target_capability: str
    gap_id: str
    hypothesis: str
    candidate_skill: str
    validation_dimensions: tuple[str, ...]
    rollback_condition: str
    evidence_ids: tuple[str, ...]

    @property
    def proposal_digest(self) -> str:
        payload={k:getattr(self,k) for k in (
            "proposal_id","target_capability","gap_id","hypothesis","candidate_skill",
            "validation_dimensions","rollback_condition","evidence_ids")}
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()


class OpenEndedCapabilityDiscovery:
    """Turn externally evidenced gaps into falsifiable skill-invention proposals."""

    def discover(self, gaps: Iterable[CapabilityGap], *, budget: int = 5) -> tuple[CapabilityInventionProposal, ...]:
        if budget < 1:
            raise ValueError("budget must be positive")
        unique={g.gap_id:g for g in gaps}
        ranked=sorted(unique.values(), key=lambda g:(-(g.severity*g.transfer_gap), -g.severity, g.gap_id))
        proposals=[]
        for gap in ranked[:budget]:
            seed="|".join((gap.gap_id,)+gap.domains+gap.failed_conditions)
            digest=hashlib.sha256(seed.encode()).hexdigest()[:16]
            capability=f"adaptive-{digest}"
            proposal_id=f"invention-{digest}"
            candidate=f"compose-{'+'.join(gap.failed_conditions[:3]) or 'missing-capability'}"
            hypothesis=(
                f"Adding {candidate} should reduce the observed transfer gap "
                f"for {', '.join(gap.domains)} without degrading unrelated domains."
            )
            dimensions=tuple(dict.fromkeys(("novel-domain","unfamiliar-tool","constraint-shift",
                                             "long-horizon","adversarial")))
            proposals.append(CapabilityInventionProposal(
                proposal_id, capability, gap.gap_id, hypothesis, candidate,
                dimensions, "rollback if fresh holdout regresses beyond the accepted threshold",
                gap.source_evidence))
        return tuple(proposals)


__all__=["CapabilityGap","CapabilityInventionProposal","OpenEndedCapabilityDiscovery"]
