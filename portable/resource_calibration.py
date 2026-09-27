"""Empirical resource outcome calibration."""
from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class ResourceObservation:
    lane:str; task_class:str; duration:float; memory_mb:int; success:bool
class ResourceCalibrator:
    def __init__(self): self._rows=[]
    def record(self,row:ResourceObservation): self._rows.append(row)
    def success_rate(self,lane:str,task_class:str)->float:
        r=[x for x in self._rows if x.lane==lane and x.task_class==task_class]
        return sum(x.success for x in r)/len(r) if r else 0.0
    def route(self,task_class:str,lanes=("local","cloud"))->str:
        scored=[(self.success_rate(l,task_class),l) for l in lanes]
        return max(scored)[1] if scored else "cloud"
