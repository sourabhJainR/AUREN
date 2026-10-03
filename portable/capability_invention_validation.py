"""Independent validation planning for invented capabilities.

Produces sealed-plan metadata for validating an invention across unseen
dimensions. It never runs the candidate skill or promotes it.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json
from typing import Sequence
from .open_ended_capability_discovery import CapabilityInventionProposal


@dataclass(frozen=True, slots=True)
class ValidationProbe:
    probe_id: str
    dimension: str
    task_family: str
    holdout: bool
    oracle_required: bool


@dataclass(frozen=True, slots=True)
class CapabilityValidationPlan:
    proposal_digest: str
    probes: tuple[ValidationProbe, ...]
    independent: bool
    fresh_holdout_required: bool
    minimum_pass_rate: float
    rollback_threshold: float

    @property
    def plan_digest(self) -> str:
        payload={"proposal_digest":self.proposal_digest,"probes":[p.__dict__ for p in self.probes],
                 "independent":self.independent,"fresh_holdout_required":self.fresh_holdout_required,
                 "minimum_pass_rate":self.minimum_pass_rate,"rollback_threshold":self.rollback_threshold}
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()


class CapabilityInventionValidator:
    """Construct independent, fresh-holdout validation plans for inventions."""

    def plan(self, proposal: CapabilityInventionProposal, task_families: Sequence[str]) -> CapabilityValidationPlan:
        families=tuple(dict.fromkeys(x.strip() for x in task_families if x and x.strip()))
        if len(families)<2:
            raise ValueError("at least two task families are required")
        if not proposal.validation_dimensions:
            raise ValueError("proposal has no validation dimensions")
        probes=[]
        for family in families:
            for dimension in proposal.validation_dimensions:
                raw=f"{proposal.proposal_digest}|{family}|{dimension}"
                digest=hashlib.sha256(raw.encode()).hexdigest()[:16]
                probes.append(ValidationProbe(f"probe-{digest}",dimension,family,True,True))
        return CapabilityValidationPlan(
            proposal.proposal_digest,tuple(probes),True,True,.75,.02
        )


__all__=["ValidationProbe","CapabilityValidationPlan","CapabilityInventionValidator"]
