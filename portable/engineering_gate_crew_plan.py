"""Graph-owned planning contract for AUREN's remaining engineering gates.

This module plans gate dependencies and specialist-crew proposals. It does not
create a second execution queue, run providers, authorize changes, or mark
external trust boundaries complete. Durable task lifetime remains with
AdaptiveRuntime/ExecutionController and canonical recovery checkpoints.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Callable, Mapping

from .state_graph import CrewPlan, build_crew_plan, default_crew_definitions


_GATE_ROWS = (
    {
        "gate_id": "post_merge_evidence",
        "title": "Post-merge checks and portable artifacts",
        "depends_on": (),
        "required_crews": ("python", "qa_reviewer"),
        "crew_goal": "Python tests regression review release portable bundle checksum artifacts",
        "exit_criteria": ("all applicable post-merge checks completed", "bundle and checksum artifacts tied to exact main SHA", "artifact IDs and digests recorded"),
        "external_evidence_required": False,
    },
    {
        "gate_id": "durable_plan_dispatch",
        "title": "Canonical plan, checkpoint, and idempotent dispatch lifecycle",
        "depends_on": ("post_merge_evidence",),
        "required_crews": ("python", "database_schema", "qa_reviewer"),
        "crew_goal": "Python persistence database migration dispatch API integration tests recovery",
        "exit_criteria": ("plan/run checkpoint mismatch fails closed", "dispatch is idempotent for identical immutable inputs", "stale context and missing checkpoint cannot enqueue work"),
        "external_evidence_required": False,
    },
    {
        "gate_id": "runtime_isolation_reconciliation",
        "title": "Production isolation and consequential-side-effect reconciliation",
        "depends_on": ("durable_plan_dispatch",),
        "required_crews": ("python", "security_privacy", "qa_reviewer"),
        "crew_goal": "Python security privacy authorization isolation credentials secrets threat tests",
        "exit_criteria": ("trusted runner context and signature trust roots are provisioned out of band", "receipt-persistence failure reconciles provider history without implicit redispatch", "negative tests prove fail-closed behavior"),
        "external_evidence_required": True,
    },
    {
        "gate_id": "provenance_learning",
        "title": "Provenance-aware learning and experiment resumption",
        "depends_on": ("durable_plan_dispatch",),
        "required_crews": ("python", "database_schema", "qa_reviewer"),
        "crew_goal": "Python experiment learning evidence persistence database regression tests",
        "exit_criteria": ("manifest pins source harness model provider split seed and evaluator configuration", "failed/cancelled/timed-out/unscored outcomes remain distinguishable", "duplicate stale contradictory and retracted receipts are handled"),
        "external_evidence_required": False,
    },
    {
        "gate_id": "safe_parallel_execution",
        "title": "Multi-crew parallel execution, context handoff, and shared budgets",
        "depends_on": ("durable_plan_dispatch",),
        "required_crews": ("python", "backend_api", "qa_reviewer"),
        "crew_goal": "Python API integration concurrency parallel tasks retry budget regression tests",
        "exit_criteria": ("parallel work is dependency- and conflict-aware", "canonical memory writes are serialized", "restart cancellation starvation and shared-budget cases are tested"),
        "external_evidence_required": False,
    },
    {
        "gate_id": "guarded_promotion",
        "title": "Independent evaluation and guarded capability promotion",
        "depends_on": ("runtime_isolation_reconciliation", "provenance_learning", "safe_parallel_execution"),
        "required_crews": ("python", "security_privacy", "qa_reviewer"),
        "crew_goal": "Python security independent evaluation regression release promotion rollback",
        "exit_criteria": ("candidate and baseline use pinned tasks and evaluation configuration", "quality safety reliability cost and latency thresholds are explicit", "promotion requires fresh independent evidence and tested rollback"),
        "external_evidence_required": True,
    },
    {
        "gate_id": "unified_dashboard",
        "title": "Graph-backed AUREN and Muxin multi-session dashboard",
        "depends_on": ("durable_plan_dispatch",),
        "required_crews": ("python", "frontend_ui", "qa_reviewer"),
        "crew_goal": "Python frontend UI UX accessibility session workflow tests",
        "exit_criteria": ("progress derives from durable run/checkpoint/evidence/CI state", "pause resume and cancel are authorized controller operations", "provider-loss stale-state and partial-failure states are visible"),
        "external_evidence_required": False,
    },
    {
        "gate_id": "operator_migration_rollback",
        "title": "Operator migration and rollback rehearsal",
        "depends_on": ("runtime_isolation_reconciliation", "guarded_promotion", "unified_dashboard"),
        "required_crews": ("python", "database_schema", "security_privacy", "qa_reviewer"),
        "crew_goal": "Python database migration rollback security review regression",
        "exit_criteria": ("backups and conflict inventory are verified", "operator executes rollback rehearsal in the target environment", "recovery evidence and residual risks are recorded"),
        "external_evidence_required": True,
    },
    {
        "gate_id": "independent_arena_campaign",
        "title": "Independent sealed-holdout and transfer evaluation campaign",
        "depends_on": ("runtime_isolation_reconciliation", "provenance_learning", "guarded_promotion"),
        "required_crews": ("python", "security_privacy", "qa_reviewer"),
        "crew_goal": "Python security independent evaluation test release holdout sealed campaign",
        "exit_criteria": ("executor and evaluator are independently operated", "sealed holdout custody and signing-key rotation are evidenced", "campaign results include transfer, regression, failures, and signed run receipts"),
        "external_evidence_required": True,
    },
)

_ALLOWED = {
    "pending": frozenset({"ready", "blocked", "cancelled"}),
    "ready": frozenset({"in_progress", "blocked", "cancelled"}),
    "in_progress": frozenset({"passed", "blocked", "failed", "cancelled"}),
    "blocked": frozenset({"ready", "cancelled"}),
    "failed": frozenset({"ready", "cancelled"}),
    "passed": frozenset(),
    "cancelled": frozenset(),
}
_STATUSES = frozenset(_ALLOWED)


@dataclass(frozen=True, slots=True)
class GateDefinition:
    gate_id: str
    title: str
    depends_on: tuple[str, ...]
    required_crews: tuple[str, ...]
    crew_goal: str
    exit_criteria: tuple[str, ...]
    external_evidence_required: bool


@dataclass(frozen=True, slots=True)
class GateWorkItem:
    gate_id: str
    crew_plan: CrewPlan
    required_crews: tuple[str, ...]
    unregistered_required_crews: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "crew_plan": self.crew_plan.to_payload(),
            "required_crews": list(self.required_crews),
            "unregistered_required_crews": list(self.unregistered_required_crews),
        }


def gate_definitions() -> tuple[GateDefinition, ...]:
    """Return the canonical, ordered gate DAG. Definitions are immutable."""
    gates = tuple(GateDefinition(**row) for row in _GATE_ROWS)
    known = {gate.gate_id for gate in gates}
    if len(known) != len(gates):
        raise ValueError("gate IDs must be unique")
    for gate in gates:
        if len(set(gate.depends_on)) != len(gate.depends_on):
            raise ValueError(f"duplicate dependency in gate {gate.gate_id}")
        missing = set(gate.depends_on) - known
        if missing:
            raise ValueError(f"gate {gate.gate_id} has unknown dependencies: {sorted(missing)}")
        if gate.gate_id in gate.depends_on:
            raise ValueError(f"gate {gate.gate_id} cannot depend on itself")
    _validate_acyclic(gates)
    return gates


def _validate_acyclic(gates: tuple[GateDefinition, ...]) -> None:
    by_id = {gate.gate_id: gate for gate in gates}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(gate_id: str) -> None:
        if gate_id in visiting:
            raise ValueError(f"gate dependency cycle includes {gate_id}")
        if gate_id in visited:
            return
        visiting.add(gate_id)
        for dependency in by_id[gate_id].depends_on:
            visit(dependency)
        visiting.remove(gate_id)
        visited.add(gate_id)

    for gate in gates:
        visit(gate.gate_id)


def build_gate_crew_plan(
    *,
    graph_digest: str,
    intent_digest: str,
    agents: Mapping[str, Any],
) -> tuple[GateWorkItem, ...]:
    """Build per-gate assignments through AUREN's canonical crew planner.

    Missing production adapters are surfaced as data; this function never
    fabricates provider execution or treats an unregistered crew as completed.
    """
    definitions = {crew.crew_id: crew for crew in default_crew_definitions()}
    result: list[GateWorkItem] = []
    for gate in gate_definitions():
        missing_contracts = set(gate.required_crews) - set(definitions)
        if missing_contracts:
            raise RuntimeError(f"canonical crew contracts missing: {sorted(missing_contracts)}")
        plan = build_crew_plan(
            f"{gate.title}. {gate.crew_goal}",
            graph_digest=graph_digest,
            intent_digest=f"{intent_digest}:{gate.gate_id}",
            agents=agents,
            crews=definitions,
        )
        assigned = {item.crew_id for item in plan.assignments}
        absent = tuple(sorted(set(gate.required_crews) - assigned))
        if absent:
            raise RuntimeError(f"crew planner omitted required crews for {gate.gate_id}: {absent}")
        unregistered = tuple(sorted(
            crew_id for crew_id in gate.required_crews
            if not next(item for item in plan.assignments if item.crew_id == crew_id).agent_registered
        ))
        result.append(GateWorkItem(gate.gate_id, plan, gate.required_crews, unregistered))
    return tuple(result)


def ready_gates(statuses: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return gates whose prerequisites are passed and which are not terminal."""
    current = dict(statuses or {})
    known = {gate.gate_id for gate in gate_definitions()}
    unknown = set(current) - known
    if unknown:
        raise ValueError(f"unknown gate statuses: {sorted(unknown)}")
    invalid = {gate: status for gate, status in current.items() if status not in _STATUSES}
    if invalid:
        raise ValueError(f"invalid gate statuses: {invalid}")
    return tuple(
        gate.gate_id for gate in gate_definitions()
        if current.get(gate.gate_id, "pending") in {"pending", "ready"}
        and all(current.get(dep, "pending") == "passed" for dep in gate.depends_on)
    )


def transition_gate(
    statuses: Mapping[str, str],
    gate_id: str,
    target: str,
    *,
    evidence_refs: tuple[str, ...] = (),
    external_evidence_verifier: Callable[[str, tuple[str, ...]], bool] | None = None,
) -> dict[str, str]:
    """Validate one status transition without owning or persisting run state.

    The trusted caller must wire external_evidence_verifier to the real operator/
    Arena trust boundary. A boolean or worker-authored status is not attestation.
    """
    definitions = {gate.gate_id: gate for gate in gate_definitions()}
    if gate_id not in definitions:
        raise KeyError(gate_id)
    if target not in _STATUSES:
        raise ValueError(f"invalid gate status: {target}")
    current = dict(statuses)
    if set(current) - set(definitions):
        raise ValueError("statuses contain unknown gate IDs")
    source = current.get(gate_id, "pending")
    if target not in _ALLOWED[source]:
        raise ValueError(f"invalid gate transition: {source} -> {target}")
    gate = definitions[gate_id]
    if target in {"ready", "in_progress", "passed"}:
        incomplete = [dep for dep in gate.depends_on if current.get(dep, "pending") != "passed"]
        if incomplete:
            raise ValueError("gate prerequisites are not passed: " + ", ".join(incomplete))
    if target == "passed":
        if not evidence_refs or any(not isinstance(ref, str) or not ref.strip() for ref in evidence_refs):
            raise ValueError("passing a gate requires non-empty evidence references")
        if len(set(evidence_refs)) != len(evidence_refs):
            raise ValueError("duplicate evidence references are not allowed")
        if gate.external_evidence_required:
            if not callable(external_evidence_verifier) or not external_evidence_verifier(gate_id, evidence_refs):
                raise PermissionError("external trust gate requires verified out-of-band evidence")
    elif evidence_refs:
        raise ValueError("evidence references are only accepted on the passed transition")
    current[gate_id] = target
    return current


def gate_plan_digest(work_items: tuple[GateWorkItem, ...]) -> str:
    payload = [item.to_payload() for item in work_items]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


__all__ = [
    "GateDefinition", "GateWorkItem", "build_gate_crew_plan", "gate_definitions",
    "gate_plan_digest", "ready_gates", "transition_gate",
]
