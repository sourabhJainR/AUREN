"""Bounded capability invention from recurring verified failure/uncertainty clusters."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Mapping

@dataclass(frozen=True)
class CapabilityInvention:
    invention_id: str
    trigger: str
    components: tuple[str,...]
    expected_effect: str
    evidence_count: int
    uncertainty: float
    priority: float
    state: str = "candidate"
    def as_dict(self):
        return {"invention_id":self.invention_id,"trigger":self.trigger,
                "components":list(self.components),"expected_effect":self.expected_effect,
                "evidence_count":self.evidence_count,"uncertainty":round(self.uncertainty,3),
                "priority":round(self.priority,3),"state":self.state}

class FailureClusterCapabilityInventor:
    """Proposes capability compositions; promotion remains benchmark/canary gated."""
    def __init__(self, *, min_samples=3, max_candidates=4):
        self.min_samples=max(2,int(min_samples)); self.max_candidates=max(1,int(max_candidates))

    @staticmethod
    def _key(row: Mapping[str,object]):
        trigger=str(row.get("trigger") or row.get("failure_pattern") or "").strip()
        components=tuple(sorted(str(x) for x in row.get("components",()) if str(x)))
        return trigger,components

    def propose(self, episodes: Iterable[Mapping[str,object]]) -> tuple[CapabilityInvention,...]:
        groups=defaultdict(list)
        for row in episodes:
            key=self._key(row)
            if not key[0] or len(key[1])<2: continue
            groups[key].append(row)
        out=[]
        for (trigger,components),rows in sorted(groups.items(), key=lambda x:(-len(x[1]),x[0])):
            if len(rows)<self.min_samples: continue
            uncertainty=sum(float(r.get("uncertainty",.5)) for r in rows)/len(rows)
            priority=max(0.0,min(1.0,(len(rows)/8.0)*.55+uncertainty*.45))
            raw=f"{trigger}|{'|'.join(components)}"
            out.append(CapabilityInvention(hashlib.sha256(raw.encode()).hexdigest()[:16],
                trigger,components,"reduce recurring failure by composing verified components",
                len(rows),uncertainty,priority))
            if len(out)>=self.max_candidates: break
        return tuple(out)

__all__=["CapabilityInvention","FailureClusterCapabilityInventor"]
