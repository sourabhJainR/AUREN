"""Active-learning and self-model primitives for AUREN.

This layer turns execution telemetry into explicit uncertainty and bounded
experiment proposals. It is provider-free and advisory: execution authority,
safety gates, and promotion remain owned by the existing runtime.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence
from .experience_router import ExperienceRouter

@dataclass(frozen=True)
class CapabilityBelief:
    capability: str
    success_rate: float
    evidence_quality: float
    confidence: float
    samples: int
    uncertainty: float
    def as_dict(self) -> dict[str, object]:
        return {"capability": self.capability, "success_rate": round(self.success_rate,3),
                "evidence_quality": round(self.evidence_quality,3), "confidence": round(self.confidence,3),
                "samples": self.samples, "uncertainty": round(self.uncertainty,3)}

@dataclass(frozen=True)
class ExperimentProposal:
    name: str
    hypothesis: str
    information_value: float
    expected_cost: float
    risk: float
    rationale: str
    @property
    def priority(self) -> float:
        return max(0.0,min(1.0,self.information_value-0.35*self.expected_cost-0.50*self.risk))
    def as_dict(self) -> dict[str, object]:
        return {"name":self.name,"hypothesis":self.hypothesis,
                "information_value":round(self.information_value,3),
                "expected_cost":round(self.expected_cost,3),"risk":round(self.risk,3),
                "priority":round(self.priority,3),"rationale":self.rationale}

@dataclass(frozen=True)
class SelfModel:
    capabilities: tuple[CapabilityBelief, ...]
    strongest: str | None
    weakest: str | None
    uncertainty: float
    def as_dict(self) -> dict[str, object]:
        return {"capabilities":[x.as_dict() for x in self.capabilities],
                "strongest":self.strongest,"weakest":self.weakest,"uncertainty":round(self.uncertainty,3)}

class ActiveLearningController:
    """Build a bounded self-model and select the highest-value safe probe."""
    def __init__(self, root: Path, *, minimum_samples: int = 3) -> None:
        self.root=Path(root); self.minimum_samples=max(1,int(minimum_samples))

    def self_model(self, *, role: str, task: str, capabilities: Sequence[str]) -> SelfModel:
        router=ExperienceRouter(self.root,minimum_samples=self.minimum_samples)
        beliefs=[]
        for capability in dict.fromkeys(str(x) for x in capabilities if str(x).strip()):
            summary=router.summarize(f"{role}:{task[:96]}:capability:{capability}")
            if summary is None:
                beliefs.append(CapabilityBelief(capability,.5,.5,0.0,0,1.0)); continue
            uncertainty=max(0.0,min(1.0,0.60*(1.0-summary.confidence)
                +0.25*summary.failure_rate+0.15*(1.0-summary.evidence_quality)))
            beliefs.append(CapabilityBelief(capability,summary.success_rate,summary.evidence_quality,
                summary.confidence,summary.samples,uncertainty))
        ordered=tuple(sorted(beliefs,key=lambda x:x.capability))
        known=[x for x in ordered if x.samples>=self.minimum_samples]
        strongest=max(known,key=lambda x:(x.success_rate,x.evidence_quality),default=None)
        weakest=min(known,key=lambda x:(x.success_rate,-x.uncertainty),default=None)
        uncertainty=sum(x.uncertainty for x in ordered)/len(ordered) if ordered else 1.0
        return SelfModel(ordered,strongest.capability if strongest else None,
                         weakest.capability if weakest else None,uncertainty)

    def propose(self, *, self_model: SelfModel, candidates: Iterable[str]) -> ExperimentProposal | None:
        by_name={x.capability:x for x in self_model.capabilities}; proposals=[]
        for name in dict.fromkeys(str(x) for x in candidates if str(x).strip()):
            belief=by_name.get(name)
            if belief is None: continue
            information=max(0.0,min(1.0,belief.uncertainty+0.20*(belief.samples==0)))
            cost=0.35 if belief.samples==0 else min(1.0,0.20+belief.samples/40.0)
            risk=max(0.05,min(0.60,0.20+0.35*(1.0-belief.evidence_quality)))
            proposals.append(ExperimentProposal(
                f"probe:{name}",f"{name} can improve evidence quality for the current workload",
                information,cost,risk,"cold-start capability probe" if belief.samples==0
                else f"reduce uncertainty from {belief.uncertainty:.2f}"))
        return max(proposals,key=lambda x:(x.priority,x.information_value,x.name)) if proposals else None

__all__=["CapabilityBelief","ExperimentProposal","SelfModel","ActiveLearningController"]
