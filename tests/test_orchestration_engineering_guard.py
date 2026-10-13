"""End-to-end enforcement tests for the orchestration engineering guard."""

from portable.orchestration import (
    Graph,
    Node,
    NodeKind,
    Orchestrator,
    RunStatus,
)


def test_high_risk_security_contract_blocks_before_any_node_runs():
    calls = []

    graph = Graph([
        Node("mutate", NodeKind.DETERMINISTIC, lambda state: calls.append("ran") or {"ok": True}, risk="high")
    ])
    run = Orchestrator(graph).run(
        "security-task",
        "Change authentication and authorization for external tool execution",
        context={"changed_paths": ["portable/security/tool_runner.py"]},
    )

    assert run.status == RunStatus.BLOCKED
    assert calls == []
    assert run.stop_reason == "engineering design guard blocked task before execution"
    assert any(item.kind == "engineering-design-review" for item in run.evidence)
    review = next(item for item in run.trajectory if item.get("event") == "engineering_design_review")
    assert review["status"] == "blocked"
    assert review["intent_digest"] == run.intent_digest


def test_medium_risk_task_records_guard_receipt_and_can_continue():
    calls = []
    graph = Graph([
        Node("inspect", NodeKind.DETERMINISTIC, lambda state: calls.append(state.get("engineering_design_review", {}).get("kind")) or {"ok": True})
    ])

    run = Orchestrator(graph).run("docs-task", "Clarify the project documentation", context={"changed_paths": ["docs/guide.md"]})

    assert run.status == RunStatus.ACCEPTED
    assert calls == ["engineering_design_review"]
    assert any(item.kind == "engineering-design-review" for item in run.evidence)
    assert any(item.get("event") == "engineering_design_review" for item in run.trajectory)


def test_explicit_security_not_applicable_reason_does_not_block():
    calls = []
    graph = Graph([
        Node("document", NodeKind.DETERMINISTIC, lambda state: calls.append("ran") or {"ok": True}, risk="high")
    ])

    run = Orchestrator(graph).run(
        "security-docs",
        "Document security principles without changing runtime behavior",
        context={
            "changed_paths": ["docs/security-principles.md"],
            "engineering_design": {
                "security": "not_applicable: documentation-only change with no runtime trust boundary changes",
                "least_privilege": "not_applicable: no permissions or tool authority are modified",
            },
        },
    )

    assert run.status == RunStatus.ACCEPTED
    assert calls == ["ran"]


def test_code_task_is_blocked_when_final_revision_was_not_verified():
    graph = Graph([
        Node("implement", NodeKind.DETERMINISTIC, lambda state: {"changed": True})
    ])
    run = Orchestrator(graph).run(
        "code-task",
        "Implement a small pure helper",
        context={
            "changed_paths": ["portable/helper.py"],
            "final_code_revision": "abc123",
            "verified_code_revision": "old456",
        },
    )
    assert run.status == RunStatus.BLOCKED
    assert run.stop_reason == "exact final code revision verification missing or stale"
    assert any(item.kind == "exact-head-verification" for item in run.evidence)


def test_code_task_accepts_only_matching_final_and_verified_revision():
    graph = Graph([
        Node("implement", NodeKind.DETERMINISTIC, lambda state: {"changed": True})
    ])
    run = Orchestrator(graph).run(
        "code-task",
        "Implement a small pure helper",
        context={
            "changed_paths": ["portable/helper.py"],
            "final_code_revision": "abc123",
            "verified_code_revision": "abc123",
        },
    )
    assert run.status == RunStatus.ACCEPTED
    receipt = next(item for item in run.trajectory if item.get("event") == "exact_head_verification")
    assert receipt["status"] == "passed"
    assert receipt["final_revision"] == receipt["verified_revision"]
