"""Bounded strategy canary/promotion state."""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from .experience_router import ExperienceRouter

@dataclass(frozen=True)
class StrategyRollout:
    strategy: str
    state: str
    samples: int
    confidence: float
    evidence_quality: float
    reason: str
    def as_dict(self):
        return {"strategy":self.strategy,"state":self.state,"samples":self.samples,
                "confidence":round(self.confidence,3),"evidence_quality":round(self.evidence_quality,3),
                "reason":self.reason}

class StrategyCanaryController:
    """Require a bounded canary gate before learned strategy promotion."""
    def __init__(self, root: Path, *, minimum_samples=3, min_confidence=0.65, min_evidence=0.70):
        self.root=Path(root); self.minimum_samples=max(1,minimum_samples)
        self.min_confidence=float(min_confidence); self.min_evidence=float(min_evidence)
    def evaluate(self, *, role, task, strategy, canary_passed=False):
        s=ExperienceRouter(self.root, minimum_samples=self.minimum_samples).summarize(
            f"{role}:{task[:96]}:execution-strategy:{strategy}")
        if s is None:
            return StrategyRollout(strategy,"candidate",0,0.0,0.0,"no observed evidence")
        if not canary_passed and (s.samples<self.minimum_samples or s.confidence<self.min_confidence):
            return StrategyRollout(strategy,"candidate",s.samples,s.confidence,s.evidence_quality,"insufficient evidence for canary")
        if not canary_passed:
            return StrategyRollout(strategy,"canary",s.samples,s.confidence,s.evidence_quality,"eligible for bounded canary")
        if s.samples>=self.minimum_samples and s.confidence>=self.min_confidence and s.evidence_quality>=self.min_evidence:
            return StrategyRollout(strategy,"promoted",s.samples,s.confidence,s.evidence_quality,"canary gate passed")
        return StrategyRollout(strategy,"rollback",s.samples,s.confidence,s.evidence_quality,"promotion gate failed; retain baseline")
    @staticmethod
    def record_state(root: Path, rollout: StrategyRollout):
        p=Path(root)/".ai-harness"/"state"/"strategy-rollouts.jsonl"; p.parent.mkdir(parents=True,exist_ok=True)
        with p.open("a",encoding="utf-8") as h: h.write(json.dumps(rollout.as_dict(),sort_keys=True)+"\n")
