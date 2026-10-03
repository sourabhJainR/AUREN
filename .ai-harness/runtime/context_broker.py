#!/usr/bin/env python3
"""Runtime Context Broker: demand-driven, scored, bounded context discovery."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Iterable, Any
import hashlib, json, os, re, time

@dataclass(frozen=True, slots=True)
class ContextCandidate:
    context_id: str; kind: str; reason: str; loader: Callable[[], str]
    relevance: float = 0.0; confidence: float = 0.8; freshness: float = 0.8
    risk: float = 0.0; cost: int = 1; required: bool = False; phase: str = "*"
    def score(self, query: str, phase: str) -> float:
        words=set(re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}",query.lower()))
        ids=set(re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}",(self.context_id+" "+self.kind).lower()))
        lexical=len(words & ids)/max(1,len(words)); phase_bonus=0.25 if self.phase in {"*",phase} else -0.25
        return (0.45*max(0,min(1,self.relevance))+0.15*max(0,min(1,self.confidence))+0.10*max(0,min(1,self.freshness))+0.15*lexical+0.15*max(0,min(1,self.risk))+phase_bonus)

@dataclass(frozen=True, slots=True)
class ContextLease:
    context_id: str; kind: str; text: str; score: float; reason: str; acquired_at: float; digest: str

class ContextBroker:
    """Demand-driven context lifecycle: discover -> score -> load -> release."""
    OPTIONAL_SCORE_FLOOR=0.60
    def __init__(self, *, budget_chars:int=12000, max_items:int=18)->None:
        if int(budget_chars) < 0: raise ValueError("context broker budget cannot be negative")
        self.budget_chars=int(budget_chars); self.max_items=max(1,int(max_items)); self._candidates={}; self._active={}; self._events=[]
    def _emit(self,event:dict[str,Any])->None:
        self._events.append(event)
        target=os.environ.get("AUREN_CONTEXT_BROKER_TELEMETRY")
        if target:
            try:
                p=__import__("pathlib").Path(target); p.parent.mkdir(parents=True,exist_ok=True)
                with p.open("a",encoding="utf-8") as f: f.write(json.dumps(event,separators=(",",":"))+"\n")
            except OSError: pass
    def register(self,candidate:ContextCandidate)->None:
        if candidate.cost<0: raise ValueError("context candidate cost cannot be negative")
        self._candidates[candidate.context_id]=candidate
    def register_many(self,candidates:Iterable[ContextCandidate])->None:
        for candidate in candidates:self.register(candidate)
    def discover(self,query:str,*,phase:str="",budget_chars:int|None=None,max_items:int|None=None)->list[ContextLease]:
        budget=self.budget_chars if budget_chars is None else max(0,int(budget_chars)); limit=self.max_items if max_items is None else max(1,int(max_items))
        ranked=sorted(((c,c.score(query,phase)) for c in self._candidates.values()),key=lambda x:(x[0].required,x[1],x[0].context_id),reverse=True)
        leases=[]; used=0
        for c,score in ranked:
            if len(leases)>=limit: break
            if not c.required and c.phase not in {"*",phase}: continue
            if not c.required and score<self.OPTIONAL_SCORE_FLOOR: continue
            text=str(c.loader() or ""); size=len(text)
            if not size: continue
            if c.required and size>budget-used: raise RuntimeError(f"required context exceeds broker budget: {c.context_id}")
            if not c.required and used+size>budget: continue
            digest=hashlib.sha256(text.encode()).hexdigest()[:16]; lease=ContextLease(c.context_id,c.kind,text,score,c.reason,time.time(),digest)
            self._active[c.context_id]=lease; leases.append(lease); used+=size
            self._emit({"event":"lease","context_id":c.context_id,"kind":c.kind,"phase":phase,"score":round(score,6),"chars":size,"digest":digest,"reason":c.reason})
        self._emit({"event":"discover","phase":phase,"query_digest":hashlib.sha256(query.encode()).hexdigest()[:16],"selected":[x.context_id for x in leases],"chars":used})
        return leases
    def release(self,context_ids:Iterable[str]|None=None)->None:
        ids=list(self._active) if context_ids is None else list(context_ids)
        for context_id in ids:
            lease=self._active.pop(context_id,None)
            if lease:self._emit({"event":"release","context_id":context_id,"digest":lease.digest})
    def active(self)->tuple[ContextLease,...]: return tuple(self._active.values())
    def telemetry(self)->dict[str,Any]: return {"candidates":len(self._candidates),"active":len(self._active),"events":list(self._events[-50:]),"budget_chars":self.budget_chars,"max_items":self.max_items}
