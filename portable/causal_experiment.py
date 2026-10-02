"""Bounded causal experiment selection for the AER cognitive loop.

The module does not execute interventions. It ranks safe, observable probes
using existing world-model calibration and current decision uncertainty.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Mapping

@dataclass(frozen=True)
class CausalHypothesis:
    name: str
    action: str
    expected_effect: str
    confidence: float
    evidence: int
    uncertainty: float

    def as_dict(self) -> dict[str, object]:
        return {"name":self.name,"action":self.action,"expected_effect":self.expected_effect,
                "confidence":round(self.confidence,3),"evidence":self.evidence,
                "uncertainty":round(self.uncertainty,3)}

@dataclass(frozen=True)
class CausalExperiment:
    hypothesis: str
    action: str
    information_gain: float
    expected_cost: float
    risk: float
    rationale: str

    @property
    def priority(self) -> float:
        return max(0.0,min(1.0,self.information_gain-0.30*self.expected_cost-0.55*self.risk))

    def as_dict(self) -> dict[str, object]:
        return {"hypothesis":self.hypothesis,"action":self.action,
                "information_gain":round(self.information_gain,3),
                "expected_cost":round(self.expected_cost,3),"risk":round(self.risk,3),
                "priority":round(self.priority,3),"rationale":self.rationale}

class CausalExperimentSelector:
    """Select the safest high-information intervention candidate."""

    def select(self, *, hypotheses: Iterable[CausalHypothesis],
               risk_budget: float = 0.40) -> CausalExperiment | None:
        candidates=[]
        budget=max(0.0,min(1.0,float(risk_budget)))
        for h in hypotheses:
            uncertainty=max(0.0,min(1.0,float(h.uncertainty)))
            confidence=max(0.0,min(1.0,float(h.confidence)))
            evidence=max(0,int(h.evidence))
            gain=max(0.0,min(1.0,uncertainty*(1.0+0.25*(evidence==0))))
            cost=max(0.10,min(1.0,0.20+0.02*evidence))
            risk=max(0.05,min(1.0,0.10+0.45*(1.0-confidence)))
            if risk>budget:
                continue
            candidates.append(CausalExperiment(
                h.name,h.action,gain,cost,risk,
                "probe the least certain causal effect within the risk budget",
            ))
        return max(candidates,key=lambda x:(x.priority,x.information_gain,x.hypothesis)) if candidates else None

__all__=["CausalHypothesis","CausalExperiment","CausalExperimentSelector"]
