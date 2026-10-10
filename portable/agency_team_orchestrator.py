"""Provider-neutral team orchestration with early verification and review gates.

The host supplies the real agent spawner. AUREN owns decomposition, dependency
ordering, bounded parallelism, evidence lineage, early gates, and fail-closed
handoffs. No model or command execution is hidden in this module.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable, Protocol, Sequence
from .agency_execution_plan import SpecialistWork, build_plan, ExecutionPlan

@dataclass(frozen=True)
class WorkUnit:
    unit_id: str; goal: str; specialist: str = "generalist-engineer"; role: str = "primary"; mutation_mode: str = "read-only"; read_paths: tuple[str, ...] = (); write_paths: tuple[str, ...] = (); depends_on: tuple[str, ...] = (); priority: int = 0
    def __post_init__(self) -> None:
        if not self.unit_id.strip() or not self.goal.strip(): raise ValueError("unit_id and goal are required")

@dataclass(frozen=True)
class AgentOutput:
    unit_id: str; status: str; deliverables: tuple[str, ...] = (); evidence: tuple[str, ...] = (); verification: tuple[str, ...] = (); findings: tuple[str, ...] = (); detail: str = ""

@dataclass(frozen=True)
class UnitGate:
    unit_id: str; verification_passed: bool; review_passed: bool; verification_digest: str; review_digest: str | None; reason: str

@dataclass(frozen=True)
class TeamRun:
    evidence_digest: str; units: tuple[WorkUnit, ...]; plan: ExecutionPlan; outputs: tuple[AgentOutput, ...]; gates: tuple[UnitGate, ...]; stopped_units: tuple[str, ...]; team_digest: str
    @property
    def passed(self) -> bool: return bool(self.gates) and all(g.verification_passed and g.review_passed for g in self.gates) and not self.stopped_units
    def as_dict(self) -> dict[str, object]:
        return {"evidence_digest": self.evidence_digest, "units": [u.__dict__ for u in self.units], "plan": self.plan.as_dict(), "outputs": [o.__dict__ for o in self.outputs], "gates": [g.__dict__ for g in self.gates], "stopped_units": list(self.stopped_units), "team_digest": self.team_digest, "passed": self.passed}

class AgentSpawner(Protocol):
    def __call__(self, unit: WorkUnit, evidence_digest: str) -> AgentOutput: ...
Verifier = Callable[[WorkUnit, AgentOutput, str], tuple[bool, str]]
Reviewer = Callable[[WorkUnit, AgentOutput, str, str], tuple[bool, str]]

class AgentTeamOrchestrator:
    """Split work into complete units, spawn agents, and gate each unit early."""
    def __init__(self, *, max_parallel: int = 4):
        if max_parallel < 1: raise ValueError("max_parallel must be positive")
        self.max_parallel = max_parallel
    @staticmethod
    def plan(units: Sequence[WorkUnit]) -> ExecutionPlan:
        by_id = {u.unit_id: u for u in units}
        if len(by_id) != len(units): raise ValueError("unit IDs must be unique")
        return build_plan(tuple(SpecialistWork(u.specialist, u.role, u.mutation_mode, u.read_paths, u.write_paths, tuple(by_id[d].specialist for d in u.depends_on if d in by_id), u.priority) for u in units))
    def run(self, units: Sequence[WorkUnit], *, evidence_digest: str, spawn: AgentSpawner, verify: Verifier, review: Reviewer) -> TeamRun:
        if not evidence_digest.strip(): raise ValueError("evidence_digest is required")
        normalized = tuple(units); by_id = {u.unit_id: u for u in normalized}
        if len(by_id) != len(normalized): raise ValueError("unit IDs must be unique")
        if any(d not in by_id for u in normalized for d in u.depends_on): raise ValueError("unknown unit dependency")
        plan = self.plan(normalized)
        dependencies = {u.unit_id: u.depends_on for u in normalized}
        completed: set[str] = set(); blocked: set[str] = set(); outputs: dict[str, AgentOutput] = {}; gates: dict[str, UnitGate] = {}; remaining = set(by_id)
        while remaining:
            ready = sorted(u for u in remaining if set(dependencies[u]).issubset(completed) and not (set(dependencies[u]) & blocked))
            if not ready: blocked.update(remaining); break
            remaining.difference_update(ready)
            parallel = [u for u in ready if by_id[u].mutation_mode == "read-only"]
            mutation = [u for u in ready if by_id[u].mutation_mode != "read-only"]
            results: list[tuple[str, AgentOutput]] = []
            if parallel:
                with ThreadPoolExecutor(max_workers=min(self.max_parallel, len(parallel))) as pool:
                    futures = {pool.submit(spawn, by_id[u], evidence_digest): u for u in parallel}
                    for future in as_completed(futures): results.append((futures[future], future.result()))
            for unit_id in mutation: results.append((unit_id, spawn(by_id[unit_id], evidence_digest)))
            for unit_id, output in sorted(results):
                outputs[unit_id] = output
                ok, verification_digest = verify(by_id[unit_id], output, evidence_digest)
                if not ok:
                    gates[unit_id] = UnitGate(unit_id, False, False, verification_digest, None, "early verification failed"); blocked.add(unit_id); blocked.update(d for d in remaining if unit_id in dependencies[d]); continue
                review_ok, review_digest = review(by_id[unit_id], output, evidence_digest, verification_digest)
                gates[unit_id] = UnitGate(unit_id, True, review_ok, verification_digest, review_digest if review_ok else None, "passed" if review_ok else "early review failed")
                if review_ok: completed.add(unit_id)
                else: blocked.add(unit_id); blocked.update(d for d in remaining if unit_id in dependencies[d])
        stopped = tuple(sorted(blocked | (set(by_id) - set(gates))))
        payload = [(u.unit_id, outputs.get(u.unit_id).__dict__ if u.unit_id in outputs else None, gates.get(u.unit_id).__dict__ if u.unit_id in gates else None) for u in normalized]
        digest = sha256(repr((evidence_digest, plan.digest(), payload)).encode()).hexdigest()[:16]
        return TeamRun(evidence_digest, normalized, plan, tuple(outputs[k] for k in sorted(outputs)), tuple(gates[k] for k in sorted(gates)), stopped, digest)

__all__ = ["AgentOutput", "AgentSpawner", "AgentTeamOrchestrator", "TeamRun", "UnitGate", "WorkUnit"]
