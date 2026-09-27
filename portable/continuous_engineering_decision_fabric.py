"""Continuous Engineering Decision Fabric.

Evidence-driven routing for real-world multi-team repositories. Repository
mutation stays behind the existing execution authority: this module only
observes state, chooses a strategy, learns outcomes, and gates promotion.
"""
from __future__ import annotations
import hashlib, json, subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Callable, Iterable, Mapping, Sequence
from .persistent_memory import PersistentMemory

def _utc() -> str: return datetime.now(timezone.utc).isoformat()
def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:20]

@dataclass(frozen=True)
class RepositoryContext:
    repo: str
    branch: str
    base_ref: str
    head_sha: str
    base_sha: str
    dirty: bool
    ahead: int
    behind: int
    changed_files: tuple[str, ...]
    legacy_constraints: tuple[str, ...] = ()
    upstream_available: bool = False

@dataclass(frozen=True)
class DecisionCandidate:
    provider: str
    tool_path: str
    verification_depth: str
    parallel: bool
    expected_duration: float
    expected_failure: float
    expected_quality: float
    expected_cost: float
    score: float
    reasons: tuple[str, ...] = ()

@dataclass(frozen=True)
class Decision:
    task_family: str
    capability: str
    selected: DecisionCandidate
    alternatives: tuple[DecisionCandidate, ...]
    impact_review_required: bool
    repository_guard: RepositoryContext | None
    rationale: tuple[str, ...]

@dataclass(frozen=True)
class CounterfactualResult:
    candidate: DecisionCandidate
    measured_quality: float
    measured_duration: float
    passed: bool
    evidence_ids: tuple[str, ...] = ()

@dataclass(frozen=True)
class CanaryRecord:
    capability: str
    candidate_id: str
    baseline_id: str
    status: str
    canary_samples: int
    pass_rate: float
    regression_count: int
    rollback_target: str | None
    evidence_ids: tuple[str, ...] = ()

class RepositoryGuard:
    """Read-only branch/worktree inspection; never mutates git state."""
    def inspect(self, root: str, *, base_ref: str = "origin/main") -> RepositoryContext:
        def git(*args: str) -> str:
            p = subprocess.run(["git","-C",root,*args],capture_output=True,text=True,timeout=10,check=False)
            if p.returncode: raise RuntimeError((p.stderr or p.stdout).strip() or "git command failed")
            return p.stdout.strip()
        branch = git("branch","--show-current") or "(detached)"
        head = git("rev-parse","HEAD")
        status = git("status","--porcelain")
        changed = tuple(sorted(line[3:].strip() for line in status.splitlines() if len(line) >= 4))
        base_sha, ahead, behind, upstream = "", 0, 0, False
        try:
            base_sha = git("rev-parse",base_ref)
            counts = git("rev-list","--left-right","--count",f"HEAD...{base_ref}").split()
            ahead, behind = int(counts[0]), int(counts[1]); upstream = True
        except RuntimeError: pass
        return RepositoryContext(root,branch,base_ref,head,base_sha,bool(status),ahead,behind,changed,(),upstream)
    @staticmethod
    def guard(context: RepositoryContext, *, allow_dirty: bool=False) -> tuple[bool,tuple[str,...]]:
        reasons=[]
        if context.dirty and not allow_dirty: reasons.append("working tree is dirty; preserve unrelated work")
        if context.behind > 0: reasons.append(f"branch is {context.behind} commit(s) behind {context.base_ref}; refresh before mutation")
        if context.ahead > 0 and context.behind > 0: reasons.append("branch diverged from base; do not auto-merge")
        return not reasons, tuple(reasons)

    @staticmethod
    def integration_plan(context: RepositoryContext) -> tuple[str, ...]:
        """Describe explicit host actions required to synchronize branches."""
        if context.dirty:
            return ("preserve or commit/stash unrelated local changes",)
        if not context.upstream_available:
            return (f"establish/read the configured base ref {context.base_ref}",)
        if context.behind and context.ahead:
            return ("review divergence", "resolve integration explicitly", "re-run impact and regression checks")
        if context.behind:
            return ("fetch upstream", f"refresh {context.branch} from {context.base_ref}", "re-run impact and regression checks")
        if context.ahead:
            return ("run verification", "open/update the integration PR")
        return ("working tree is synchronized; proceed to scoped planning",)

class ContinuousEngineeringDecisionFabric:
    """Persist routing observations and make bounded evidence-driven decisions."""
    def __init__(self,memory:PersistentMemory,project:str)->None:
        if not isinstance(memory,PersistentMemory): raise TypeError("memory must be PersistentMemory")
        if not project.strip(): raise ValueError("project is required")
        self.memory,self.project=memory,project.strip()
        with memory._lock,memory._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS decision_observations(
              project TEXT NOT NULL,task_family TEXT NOT NULL,capability TEXT NOT NULL,
              provider TEXT NOT NULL,tool_path TEXT NOT NULL,parallel INTEGER NOT NULL,
              success INTEGER NOT NULL,duration REAL NOT NULL,cost REAL NOT NULL,
              quality REAL NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decision_canaries(
              project TEXT NOT NULL,capability TEXT NOT NULL,candidate_id TEXT NOT NULL,
              baseline_id TEXT NOT NULL,status TEXT NOT NULL,samples INTEGER NOT NULL,
              pass_rate REAL NOT NULL,regressions INTEGER NOT NULL,rollback_target TEXT,
              evidence_json TEXT NOT NULL,updated_at TEXT NOT NULL,
              PRIMARY KEY(project,capability,candidate_id));
            """)
    def observe(self,task_family:str,capability:str,provider:str,tool_path:str,*,success:bool,duration_seconds:float,cost:float,quality:float,parallel:bool=False)->None:
        if min(duration_seconds,cost)<0 or not 0<=quality<=1: raise ValueError("invalid observation metrics")
        with self.memory._lock,self.memory._connect() as db:
            db.execute("INSERT INTO decision_observations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (self.project,task_family,capability,provider,tool_path,int(parallel),int(success),float(duration_seconds),float(cost),float(quality),_utc()))
    def _metrics(self,tf:str,cap:str,provider:str,tool:str,parallel:bool)->tuple[float,float,float,float,int]:
        with self.memory._lock,self.memory._connect() as db:
            rows=db.execute("""SELECT success,duration,cost,quality FROM decision_observations
              WHERE project=? AND task_family=? AND capability=? AND provider=? AND tool_path=? AND parallel=?""",
              (self.project,tf,cap,provider,tool,int(parallel))).fetchall()
        if not rows:return .5,60.,0.,.5,0
        return mean(r[0] for r in rows),mean(r[1] for r in rows),mean(r[2] for r in rows),mean(r[3] for r in rows),len(rows)
    def decide(self,task_family:str,capability:str,candidates:Sequence[Mapping[str,Any]],*,impact_review_required:bool=False,repository:RepositoryContext|None=None)->Decision:
        if not candidates: raise ValueError("at least one decision candidate is required")
        rows=[]
        for raw in candidates:
            provider,tool=str(raw["provider"]),str(raw.get("tool_path",raw["provider"]))
            depth,parallel=str(raw.get("verification_depth","standard")),bool(raw.get("parallel",False))
            success,duration,cost,quality,samples=self._metrics(task_family,capability,provider,tool,parallel)
            failure=1-success; declared=raw.get("expected",{})
            if samples==0:
                duration=float(declared.get("duration",duration)); cost=float(declared.get("cost",cost))
                quality=float(declared.get("quality",quality)); failure=float(declared.get("failure",failure))
            depth_factor={"standard":1.0,"deep":1.15,"independent":1.30}.get(depth,1.20)
            legacy = tuple(repository.legacy_constraints) if repository else ()
            if impact_review_required or (repository and repository.dirty): depth_factor*=1.15
            # Legacy constraints never disappear because a faster route exists.
            # They increase verification/risk cost unless the candidate explicitly
            # declares compatibility evidence for the named constraints.
            compatible = set(str(x) for x in raw.get("legacy_compatible", ()))
            unmet_legacy = tuple(x for x in legacy if x not in compatible)
            if unmet_legacy:
                depth_factor*=1.20
                failure=min(.95, failure+.05*len(unmet_legacy))
            score=quality*100-failure*45-duration*.08-cost*2-(depth_factor-1)*5
            rows.append(DecisionCandidate(provider,tool,depth,parallel,round(duration,4),round(failure,4),round(quality,4),round(cost,4),round(score,4),
                ((f"{samples} historical observations","verification depth adjusted for repository risk") if samples else ("cold-start; declared defaults used",))))
        ordered=tuple(sorted(rows,key=lambda x:(-x.score,x.expected_failure,x.expected_duration,x.provider)))
        rationale=("measured success, duration, cost and quality drive routing","repository risk increases verification depth","legacy constraints remain active unless compatibility evidence is supplied")
        if repository and repository.behind:rationale+=("base divergence requires explicit refresh before mutation",)
        return Decision(task_family,capability,ordered[0],ordered[1:],impact_review_required,repository,rationale)
    def compare_counterfactuals(self,candidates:Sequence[DecisionCandidate],execute_and_measure:Callable[[DecisionCandidate],CounterfactualResult])->tuple[CounterfactualResult,...]:
        return tuple(execute_and_measure(c) for c in candidates)
    def canary(self,capability:str,candidate_id:str,baseline_id:str,results:Iterable[CounterfactualResult],*,min_samples:int=5,minimum_quality:float=.9)->CanaryRecord:
        rows=tuple(results); evidence=tuple(dict.fromkeys(e for row in rows for e in row.evidence_ids))
        if len(rows)<min_samples:return CanaryRecord(capability,candidate_id,baseline_id,"hold",len(rows),0.,0,baseline_id,evidence)
        passed=[r for r in rows if r.passed and r.measured_quality>=minimum_quality]; regressions=sum(not r.passed for r in rows)
        rate=len(passed)/len(rows); status="promote" if rate>=.9 and regressions==0 else "rollback"
        record=CanaryRecord(capability,candidate_id,baseline_id,status,len(rows),round(rate,4),regressions,baseline_id,evidence)
        with self.memory._lock,self.memory._connect() as db:
            db.execute("""INSERT INTO decision_canaries VALUES(?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(project,capability,candidate_id) DO UPDATE SET status=excluded.status,
              samples=excluded.samples,pass_rate=excluded.pass_rate,regressions=excluded.regressions,
              rollback_target=excluded.rollback_target,evidence_json=excluded.evidence_json,updated_at=excluded.updated_at""",
              (self.project,capability,candidate_id,baseline_id,status,len(rows),rate,regressions,baseline_id,json.dumps(evidence),_utc()))
        return record
    def planning_feedback(self,decision:Decision,outcome:Mapping[str,Any])->dict[str,Any]:
        return {"task_family":decision.task_family,"capability":decision.capability,"selected":asdict(decision.selected),
                "outcome":dict(outcome),"routing_evidence_digest":_digest({"decision":asdict(decision),"outcome":dict(outcome)})}

__all__=["RepositoryContext","RepositoryGuard","DecisionCandidate","Decision","CounterfactualResult","CanaryRecord","ContinuousEngineeringDecisionFabric"]
