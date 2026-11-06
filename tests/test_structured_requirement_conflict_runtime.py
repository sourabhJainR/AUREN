"""Host-bound structured requirement conflicts gate graph admission."""
from portable.orchestration import Graph, Node, NodeKind, Orchestrator, RunStatus
from portable.state_graph import StateGraph
from portable.requirement_conflicts import RequirementFact


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
