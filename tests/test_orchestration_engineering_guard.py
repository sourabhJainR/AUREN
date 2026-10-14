"""End-to-end enforcement tests for the orchestration engineering guard."""

import hashlib
import json

from portable.engineering_design_guard import EngineeringDesignGuard, PRINCIPLE_FAMILY_IDS
from portable.orchestration import (
    Graph,
    Node,
    NodeKind,
    Orchestrator,
    RunStatus,
)


def principle_verification_receipt(intent_digest: str, code_revision: str):
    payload = {
        "schema_version": "auren-engineering-principle-verification-v1",
        "intent_digest": intent_digest,
        "code_revision": code_revision,
        "reviewer_id": "independent-principle-reviewer",
        "reviewer_version": "reviewer-v1",
        "families": {
            family_id: {
                "status": "verified",
                "reason": f"Reviewed and verified {family_id} against the final source revision.",
                "evidence_refs": [f"evidence:{family_id}:{code_revision}"],
            }
            for family_id in PRINCIPLE_FAMILY_IDS
        },
    }
    payload["digest"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


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
                "responsibility": "not_applicable: documentation-only change; no runtime module responsibility changes",
                "dependency_direction": "not_applicable: documentation-only change; no dependencies are changed",
                "compatibility": "not_applicable: no runtime contract or caller behavior changes",
                "usage_compatibility": "not_applicable: no existing callers, APIs, or workflow usage changes",
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


def test_final_principle_verification_is_required_and_bound_to_exact_revision():
    intent = "Implement a small pure helper"
    revision = "candidate-abc123"
    graph = Graph([
        Node("implement", NodeKind.DETERMINISTIC, lambda state: {"changed": True})
    ])
    base_context = {
        "changed_paths": ["portable/helper.py"],
        "final_code_revision": revision,
        "verified_code_revision": revision,
        "require_principle_verification": True,
    }

    missing = Orchestrator(graph).run("principle-task-missing", intent, context=base_context)
    assert missing.status == RunStatus.BLOCKED
    assert missing.stop_reason == "final engineering-principle verification missing, stale, or incomplete"
    assert any(item.kind == "engineering-principle-verification" for item in missing.evidence)

    receipt = principle_verification_receipt(Orchestrator.intent_digest(intent), revision)
    verified = Orchestrator(graph).run(
        "principle-task-verified",
        intent,
        context={**base_context, "principle_verification": receipt},
    )
    assert verified.status == RunStatus.ACCEPTED, (verified.stop_reason, verified.trajectory)
    evidence = next(item for item in verified.evidence if item.kind == "engineering-principle-verification")
    payload = json.loads(evidence.summary)
    assert payload["status"] == "passed"
    assert payload["receipt_digest"] == receipt["digest"]
    assert set(payload["receipt"]["families"]) == set(PRINCIPLE_FAMILY_IDS)


def test_final_principle_verification_rejects_stale_revision_and_missing_family():
    intent = "Implement a small pure helper"
    revision = "candidate-abc123"
    receipt = principle_verification_receipt(Orchestrator.intent_digest(intent), revision)
    receipt["families"].pop(PRINCIPLE_FAMILY_IDS[-1])
    payload = {key: value for key, value in receipt.items() if key != "digest"}
    receipt["digest"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    validation = EngineeringDesignGuard.validate_principle_verification_record(
        receipt, intent_digest=Orchestrator.intent_digest(intent), code_revision=revision
    )
    assert not validation["valid"]
    assert any("all 12 canonical families" in blocker for blocker in validation["blockers"])

    malformed = principle_verification_receipt(Orchestrator.intent_digest(intent), revision)
    malformed["families"][PRINCIPLE_FAMILY_IDS[0]]["status"] = []
    malformed_payload = {key: value for key, value in malformed.items() if key != "digest"}
    malformed["digest"] = hashlib.sha256(
        json.dumps(malformed_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    validation = EngineeringDesignGuard.validate_principle_verification_record(
        malformed, intent_digest=Orchestrator.intent_digest(intent), code_revision=revision
    )
    assert not validation["valid"]
    assert any("invalid verification status" in blocker for blocker in validation["blockers"])

    stale = principle_verification_receipt(Orchestrator.intent_digest(intent), "old-revision")
    validation = EngineeringDesignGuard.validate_principle_verification_record(
        stale, intent_digest=Orchestrator.intent_digest(intent), code_revision=revision
    )
    assert not validation["valid"]
    assert any("exact final code revision" in blocker for blocker in validation["blockers"])


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
