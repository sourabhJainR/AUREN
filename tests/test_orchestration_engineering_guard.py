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

    run = Orchestrator(graph).run(
        "docs-task",
        "Clarify the project documentation",
        context={"changed_paths": ["docs/guide.md"], "code_revision": "abc1234"},
    )

    assert run.status == RunStatus.ACCEPTED
    assert calls == ["engineering_design_review"]
    evidence = next(item for item in run.evidence if item.kind == "engineering-design-review")
    import json
    payload = json.loads(evidence.summary)
    assert payload["changed_paths"] == ["docs/guide.md"]
    assert payload["risk"] == "medium"
    assert payload["code_revision"] == "abc1234"
    assert payload["digest"]
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
                "architecture": "not_applicable: documentation-only change; no module or dependency boundaries change",
                "compatibility": "not_applicable: no runtime contract or caller behavior changes",
                "rollout": "not_applicable: documentation-only change; no migration or staged deployment",
                "resilience": "not_applicable: no runtime dependency, timeout, retry, or recovery behavior changes",
                "timeout_budget": "not_applicable: documentation-only change; no runtime deadlines or external calls",
                "retry_budget": "not_applicable: documentation-only change; no retry behavior changes",
                "recovery": "not_applicable: documentation-only change; no failure recovery path changes",
            },
        },
    )

    assert run.status == RunStatus.ACCEPTED, (run.stop_reason, run.trajectory)
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

# Keep the suite runnable by the repository's unittest-based CI entrypoint.
import unittest


class OrchestrationEngineeringGuardTests(unittest.TestCase):
    def test_high_risk_blocks_before_node_runs_case(self):
        test_high_risk_security_contract_blocks_before_any_node_runs()

    def test_medium_risk_records_receipt_case(self):
        test_medium_risk_task_records_guard_receipt_and_can_continue()

    def test_security_not_applicable_case(self):
        test_explicit_security_not_applicable_reason_does_not_block()

    def test_code_task_requires_exact_revision_case(self):
        test_code_task_is_blocked_when_final_revision_was_not_verified()

    def test_code_task_accepts_matching_revision_case(self):
        test_code_task_accepts_only_matching_final_and_verified_revision()
