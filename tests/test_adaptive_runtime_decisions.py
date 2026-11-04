from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from portable.adaptive_runtime import AdaptiveRuntime
from portable.durable_approval import DurableApprovalStore
from portable.engineering_plan import EngineeringPlan, PlanTask
from portable.engineering_plan_recovery import EngineeringPlanRecoveryBridge
from portable.engineering_plan_store import EngineeringPlanStore
from portable.orchestration import Graph, Node, NodeKind


def make_runtime(root: Path) -> AdaptiveRuntime:
    graph = Graph([Node("work", NodeKind.DETERMINISTIC, lambda _state: {"ready": True})])
    return AdaptiveRuntime(graph, state_root=root / "runtime")


def test_decision_recording_requires_the_trusted_plan_composition_root(tmp_path):
    runtime = make_runtime(tmp_path)
    with pytest.raises(RuntimeError, match="must be configured"):
        runtime.record_engineering_decision(
            run_id="decision-runtime", decision_id="scope", expected_revision=0,
            plan_digest="a" * 64, run_revision=0, decision_type="assumption",
            question="Use fixture data?", options=("yes", "no"), selected_option="yes",
            rationale="No live credentials are configured.", actor_id="user:owner",
        )


def test_decision_recording_is_denied_without_host_actor_authentication(tmp_path):
    runtime = make_runtime(tmp_path)
    store = EngineeringPlanStore(tmp_path / "plans.sqlite3")
    bridge = EngineeringPlanRecoveryBridge(store=store, ledger=runtime.recovery_ledger)
    approvals = DurableApprovalStore(runtime.execution_controller)
    runtime.configure_trusted_plan_dispatch(
        store=store,
        recovery_bridge=bridge,
        approval_store=approvals,
        budget_resolver=lambda *_args: {},
        approval_resolver=lambda *_args: None,
        assignment_resolver=lambda _context: None,
    )
    plan = EngineeringPlan(
        run_id="unauthenticated-decision",
        goal="Build a testable application",
        requirements=("Use fixture data.",),
        source_revision="b" * 40,
        tasks=(PlanTask("data", "Prepare fixture data", ("fixture is valid",), "remove fixture integration"),),
    )
    run = store.create(plan)
    with pytest.raises(PermissionError, match="authentication is not configured"):
        runtime.record_engineering_decision(
            run_id=plan.run_id, decision_id="fixture-data", expected_revision=0,
            plan_digest=plan.digest, run_revision=run.revision,
            decision_type="assumption", question="Use fixtures?", options=("yes", "no"),
            selected_option="yes", rationale="No live provider is configured.",
            actor_id="user:spoof",
        )


def test_adaptive_runtime_uses_the_canonical_plan_database_for_decisions(tmp_path):
    runtime = make_runtime(tmp_path)
    store = EngineeringPlanStore(tmp_path / "plans.sqlite3")
    bridge = EngineeringPlanRecoveryBridge(store=store, ledger=runtime.recovery_ledger)
    approvals = DurableApprovalStore(runtime.execution_controller)
    runtime.configure_trusted_plan_dispatch(
        store=store,
        recovery_bridge=bridge,
        approval_store=approvals,
        budget_resolver=lambda *_args: {},
        approval_resolver=lambda *_args: None,
        assignment_resolver=lambda _context: None,
        decision_authorizer=lambda decision: "user:owner" if decision.get("decision_id") == "fixture-data" else None,
    )
    plan = EngineeringPlan(
        run_id="decision-runtime",
        goal="Build a testable application",
        requirements=("Use fixture data without provider credentials.",),
        source_revision="a" * 40,
        tasks=(PlanTask("data", "Prepare fixture data", ("fixture is valid",), "remove fixture integration"),),
    )
    run = store.create(plan)

    receipt = runtime.record_engineering_decision(
        run_id=plan.run_id,
        decision_id="fixture-data",
        expected_revision=0,
        plan_digest=plan.digest,
        run_revision=run.revision,
        decision_type="assumption",
        question="May the MVP use fixture data until provider access is approved?",
        options=("Use deterministic fixtures", "Block the MVP pending provider access"),
        selected_option="Use deterministic fixtures",
        rationale="This keeps the test workflow reproducible without claiming live data.",
        rejected_alternatives=(("Block the MVP pending provider access", "It blocks unrelated UI and contract work."),),
        affected_task_ids=("data",),
        actor_id="user:owner",
    )

    assert runtime.engineering_decision_store.plan_store.database_path == store.database_path
    assert receipt.plan_digest == plan.digest
    assert runtime.engineering_decision_store.latest(plan.run_id, "fixture-data").record_digest == receipt.record_digest

    with pytest.raises(PermissionError, match="does not match"):
        runtime.record_engineering_decision(
            run_id=plan.run_id,
            decision_id="fixture-data",
            expected_revision=0,
            plan_digest=plan.digest,
            run_revision=run.revision,
            decision_type="assumption",
            question="May the MVP use fixture data until provider access is approved?",
            options=("Use deterministic fixtures", "Block the MVP pending provider access"),
            selected_option="Use deterministic fixtures",
            rationale="No live credentials are configured.",
            rejected_alternatives=(("Block the MVP pending provider access", "It blocks unrelated UI work."),),
            affected_task_ids=("data",),
            actor_id="user:spoof",
        )
