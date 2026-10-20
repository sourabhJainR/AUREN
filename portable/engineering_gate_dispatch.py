"""Compile the graph-owned engineering gate DAG into AUREN's canonical durable plan.

This is an adapter, not a new queue or state store. Persistence, checkpoint binding,
dispatch idempotency, cancellation and recovery remain owned by EngineeringPlanStore,
EngineeringPlanRecoveryBridge and EngineeringPlanDispatch.
"""
from __future__ import annotations

from typing import Any, Mapping

from .engineering_gate_crew_plan import build_gate_crew_plan, gate_definitions, gate_plan_digest
from .engineering_plan import EngineeringPlan, EngineeringPlanRun, PlanTask, new_plan_run


def build_gate_engineering_plan(
    *,
    run_id: str,
    source_revision: str,
    graph_digest: str,
    intent_digest: str,
    agents: Mapping[str, Any],
    budget: Mapping[str, int] | None = None,
) -> tuple[EngineeringPlan, EngineeringPlanRun]:
    """Compile gate dependencies and crew-plan identity into a versioned plan/run.

    Only dependency-free tasks start ready. All other tasks stay pending until
    canonical plan transitions prove every prerequisite passed with evidence.
    """
    if not isinstance(source_revision, str) or not source_revision.strip():
        raise ValueError("source_revision must be an immutable source revision")
    gates = gate_definitions()
    crew_plans = build_gate_crew_plan(
        graph_digest=graph_digest, intent_digest=intent_digest, agents=agents,
    )
    crews_by_gate = {item.gate_id: item for item in crew_plans}
    graph_binding = gate_plan_digest(crew_plans)
    tasks = tuple(
        PlanTask(
            task_id=gate.gate_id,
            title=gate.title,
            acceptance_criteria=gate.exit_criteria,
            rollback_plan=(
                "Stop dispatch, preserve checkpoints and evidence, reconcile ambiguous "
                "provider effects, and use the operator-approved rollback procedure; "
                "never auto-redispatch a consequential side effect."
            ),
            depends_on=gate.depends_on,
            risk="critical" if gate.external_evidence_required else "high",
        )
        for gate in gates
    )
    requirements = (
        "Gate execution must use the canonical ExecutionController and recovery bridge.",
        "Only evidence-backed passed prerequisites unlock dependent gates.",
        "External trust gates require independently verified evidence before pass.",
        "Graph digest: " + graph_digest,
        "Intent digest: " + intent_digest,
        "Gate crew-plan digest: " + graph_binding,
        "Per-gate crew plan digests: " + ";".join(
            gate_id + "=" + crews_by_gate[gate_id].crew_plan.digest
            for gate_id in sorted(crews_by_gate)
        ),
    )
    plan = EngineeringPlan(
        run_id=run_id,
        goal="Execute AUREN engineering gates through canonical durable graph dispatch",
        requirements=requirements,
        tasks=tasks,
        source_revision=source_revision.strip(),
        policy_digest=graph_digest,
        budget=dict(budget or {"attempts": 24, "dispatches": 24}),
    )
    run = new_plan_run(plan)
    for gate in gates:
        if not gate.depends_on:
            run = run.transition_task(
                gate.gate_id, "ready", plan=plan,
                reason="dependency-free root gate ready for canonical dispatch",
            )
    return plan, run


__all__ = ["build_gate_engineering_plan"]
