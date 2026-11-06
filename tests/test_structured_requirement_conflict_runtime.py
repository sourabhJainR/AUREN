"""Host-bound structured requirement conflicts gate graph admission."""
import tempfile
from pathlib import Path
from portable.orchestration import Graph, Node, NodeKind, Orchestrator, RunStatus
from portable.state_graph import StateGraph
from portable.requirement_conflicts import RequirementFact, RequirementConflictAnalyzer
from portable.engineering_decision_store import EngineeringDecisionStore
from portable.engineering_plan import EngineeringPlan, PlanTask
from portable.engineering_plan_store import EngineeringPlanStore


def _resolver(intake, _graph_digest, *, malformed=False):
    statements = intake["statements"]
    facts = [
        RequirementFact(
            f"statement:{statements[0]['index']}",
            "market_data_latency",
            "real_time",
            statements[0]["text"],
        ),
        RequirementFact(
            f"statement:{statements[1]['index']}",
            "market_data_latency",
            "delayed",
            statements[1]["text"],
        ),
    ]
    if malformed:
        facts[0] = RequirementFact(
            facts[0].statement_id, facts[0].key, facts[0].value, "invented source statement",
        )
    return {
        "facts": facts,
        "not_applicable_statement_ids": [],
        "workstream_dependencies": {
            "quote_adapter": ["market_data_latency"],
            "news_adapter": [],
        },
    }


def _graph(calls):
    return Graph([
        Node("quote_adapter", NodeKind.DETERMINISTIC, lambda _state: calls.append("quotes") or {"ok": True}),
        Node("news_adapter", NodeKind.DETERMINISTIC, lambda _state: calls.append("news") or {"ok": True}),
    ])


def test_structured_conflict_blocks_only_affected_graph_workstream():
    calls = []
    seen = {}

    def verify_scope(review, mapping, graph_digest):
        seen["review"] = review
        seen["mapping"] = mapping
        return (
            len(graph_digest) == 64
            and set(mapping) == {
                item["id"] for item in review["structured_requirement_conflicts"]["conflicts"]
            }
            and mapping[next(iter(mapping))] == ("quote_adapter",)
        )

    run = Orchestrator(
        _graph(calls),
        requirement_conflict_resolver=_resolver,
        conflict_scope_verifier=verify_scope,
    ).run("structured-conflict", "Build quotes. Use delayed quotes.")

    assert run.status == RunStatus.BLOCKED
    assert calls == ["news"]
    assert run.results["quote_adapter"].status.value == "blocked"
    assert run.results["news_adapter"].status.value == "passed"
    report = next(
        item for item in run.evidence
        if item.kind == "structured-requirement-conflict-analysis"
    )
    assert '"status":"analyzed"' in report.summary
    assert seen["review"]["structured_requirement_conflicts"]["conflicts"][0]["recommended_option"] == "accept_delayed"


def test_exact_host_verified_resolution_unblocks_conflict():
    calls = []
    from portable.requirement_conflicts import RequirementConflictAnalyzer

    # The resolver contract is exact: extra keys are rejected. This test uses
    # a precomputed deterministic ID from the same source-bound facts.
    intake_text = "Build quotes. Use delayed quotes."
    from portable.engineering_requirement_intake import parse_engineering_request
    intake = parse_engineering_request(intake_text)
    parsed = _resolver(intake, "")
    conflict_id = RequirementConflictAnalyzer.analyze(parsed["facts"]).conflicts[0].conflict_id
    run = Orchestrator(
        _graph(calls),
        requirement_conflict_resolver=_resolver,
        conflict_resolution_verifier=lambda key, option: key == conflict_id and option == "accept_delayed",
    ).run(
        "structured-conflict-resolved-exact",
        intake_text,
        context={"preimplementation_resolutions": {conflict_id: "accept_delayed"}},
    )
    assert run.status == RunStatus.ACCEPTED
    assert calls == ["quotes", "news"]


def test_unbound_source_statement_fails_closed_before_any_node_runs():
    calls = []
    run = Orchestrator(
        _graph(calls),
        requirement_conflict_resolver=lambda intake, digest: _resolver(intake, digest, malformed=True),
    ).run("structured-conflict-unbound", "Build quotes. Use delayed quotes.")
    assert run.status == RunStatus.BLOCKED
    assert calls == []
    assert "structured requirement conflict analysis failed closed" in run.stop_reason


def test_partial_dimension_coverage_fails_closed_before_any_node_runs():
    calls = []

    def partial(intake, _graph_digest):
        return {
            "facts": [
                RequirementFact(
                    "statement:0", "unknown_dimension", "enabled", intake["statements"][0]["text"],
                ),
                RequirementFact(
                    "statement:1", "market_data_latency", "delayed", intake["statements"][1]["text"],
                ),
            ],
            "not_applicable_statement_ids": [],
            "workstream_dependencies": {
                "quote_adapter": ["unknown_dimension"],
                "news_adapter": ["market_data_latency"],
            },
        }

    run = Orchestrator(
        _graph(calls), requirement_conflict_resolver=partial,
    ).run("structured-conflict-partial", "Build quotes. Use delayed quotes.")
    assert run.status == RunStatus.BLOCKED
    assert calls == []


def test_resolver_must_be_installed_before_first_run():
    calls = []
    orchestrator = Orchestrator(_graph(calls))
    orchestrator.run("no-resolver", "Do safe independent work.")
    try:
        orchestrator.configure_requirement_conflict_resolver(_resolver)
    except RuntimeError as exc:
        assert "before graph execution" in str(exc)
    else:
        raise AssertionError("late resolver configuration must be rejected")


def test_structured_conflict_is_reflected_in_preimplementation_review_trajectory():
    calls = []
    run = Orchestrator(
        _graph(calls),
        requirement_conflict_resolver=_resolver,
        conflict_scope_verifier=lambda _review, _mapping, _digest: True,
    ).run("structured-conflict-audit-status", "Build quotes. Use delayed quotes.")

    review_events = [
        item for item in run.trajectory
        if item.get("event") == "preimplementation_review"
    ]
    assert len(review_events) == 1
    assert review_events[0]["status"] == "blocked"
    assert run.status == RunStatus.BLOCKED
    assert calls == ["news"]



def test_state_graph_forwards_host_conflict_resolver_and_scope_verifier():
    calls = []
    graph = _graph(calls)

    def verify_scope(review, mapping, graph_digest):
        return (
            bool(review.get("structured_requirement_conflicts"))
            and len(graph_digest) == 64
            and tuple(mapping.values()) == (("quote_adapter",),)
        )

    state_graph = StateGraph(
        graph,
        conflict_scope_verifier=verify_scope,
        requirement_conflict_resolver=_resolver,
    )
    run = state_graph.run("state-graph-conflict", "Build quotes. Use delayed quotes.")
    assert run.status == RunStatus.BLOCKED
    assert calls == ["news"]



def test_existing_decision_store_is_the_only_authority_that_unblocks_conflict():
    calls = []
    intake_text = "Build quotes. Use delayed quotes."
    from portable.engineering_requirement_intake import parse_engineering_request

    intake = parse_engineering_request(intake_text)
    parsed = _resolver(intake, "")
    conflict = RequirementConflictAnalyzer.analyze(parsed["facts"]).conflicts[0]

    with tempfile.TemporaryDirectory() as directory:
        plan_store = EngineeringPlanStore(Path(directory) / "plans.sqlite3")
        plan = EngineeringPlan(
            run_id="structured-conflict-run",
            goal="Deliver source-backed quote and news adapters",
            requirements=("Resolve quote freshness conflict",),
            source_revision="a" * 40,
            tasks=(
                PlanTask("quote_adapter", "Implement quote adapter", ("freshness is explicit",), "revert quote adapter"),
                PlanTask("news_adapter", "Implement news adapter", ("news provenance is present",), "revert news adapter"),
            ),
        )
        plan_run = plan_store.create(plan)
        decision_store = EngineeringDecisionStore(
            plan_store, actor_authorizer=lambda _decision: "user:owner",
        )
        decision_store.record(
            run_id=plan.run_id,
            decision_id="quote-freshness-choice",
            expected_revision=0,
            plan_digest=plan.digest,
            run_revision=plan_run.revision,
            decision_type="conflict_resolution",
            question=conflict.title,
            options=("accept_delayed", "use_realtime_provider"),
            selected_option="accept_delayed",
            rationale="The MVP uses delayed quotes and displays their source timestamp.",
            rejected_alternatives=(("use_realtime_provider", "Paid real-time licensing is not approved for this MVP."),),
            affected_task_ids=("quote_adapter",),
            actor_id="user:owner",
        )
        verifier = decision_store.conflict_resolution_verifier(
            run_id=plan.run_id,
            decision_ids={conflict.conflict_id: "quote-freshness-choice"},
        )
        run = Orchestrator(
            _graph(calls),
            requirement_conflict_resolver=_resolver,
            conflict_resolution_verifier=verifier,
        ).run(
            "structured-conflict-durable-decision",
            intake_text,
            context={"preimplementation_resolutions": {conflict.conflict_id: "accept_delayed"}},
        )
        assert run.status == RunStatus.ACCEPTED
        assert calls == ["quotes", "news"]
        assert decision_store.latest(plan.run_id, "quote-freshness-choice").current_binding is True



def test_host_verifier_cannot_approve_an_undeclared_conflict_option():
    calls = []

    def verify_scope(review, mapping, graph_digest):
        return (
            len(graph_digest) == 64
            and set(mapping) == {
                item["id"] for item in review["structured_requirement_conflicts"]["conflicts"]
            }
        )

    from portable.requirement_conflicts import RequirementConflictAnalyzer
    from portable.engineering_requirement_intake import parse_engineering_request
    intake = parse_engineering_request("Build quotes. Use delayed quotes.")
    conflict_id = RequirementConflictAnalyzer.analyze(_resolver(intake, "")["facts"]).conflicts[0].conflict_id
    run = Orchestrator(
        _graph(calls),
        requirement_conflict_resolver=_resolver,
        conflict_scope_verifier=verify_scope,
        conflict_resolution_verifier=lambda _key, _option: True,
    ).run(
        "structured-conflict-invalid-choice",
        "Build quotes. Use delayed quotes.",
        context={"preimplementation_resolutions": {conflict_id: "invented_override"}},
    )
    assert run.status == RunStatus.BLOCKED
    assert calls == ["news"]
    conflict = run.results["quote_adapter"]
    assert conflict.status.value == "blocked"
