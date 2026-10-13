from portable.engineering_design_guard import (
    DesignDimension,
    EngineeringDesignGuard,
    FindingSeverity,
)



def test_receipt_digest_binds_canonical_changed_paths_and_risk():
    base = EngineeringDesignGuard.review(
        intent="Update project metadata",
        changed_paths=["README.md", "VERSION"],
        risk="medium",
        design={"architecture": "documentation and version metadata only"},
    )
    reordered = EngineeringDesignGuard.review(
        intent="Update project metadata",
        changed_paths=["VERSION", "README.md", "README.md"],
        risk=" MEDIUM ",
        design={"architecture": "documentation and version metadata only"},
    )
    different_scope = EngineeringDesignGuard.review(
        intent="Update project metadata",
        changed_paths=["README.md", "VERSION.json"],
        risk="medium",
        design={"architecture": "documentation and version metadata only"},
    )
    different_risk = EngineeringDesignGuard.review(
        intent="Update project metadata",
        changed_paths=["README.md", "VERSION"],
        risk="low",
        design={"architecture": "documentation and version metadata only"},
    )

    assert base.receipt_digest == reordered.receipt_digest
    assert base.changed_paths == ("README.md", "VERSION")
    different_revision = EngineeringDesignGuard.review(
        intent="Update project metadata",
        changed_paths=["README.md", "VERSION"],
        risk="medium",
        code_revision="abc1234",
        design={"architecture": "documentation and version metadata only"},
    )
    bound_revision = EngineeringDesignGuard.review(
        intent="Update project metadata",
        changed_paths=["README.md", "VERSION"],
        risk="medium",
        code_revision="def5678",
        design={"architecture": "documentation and version metadata only"},
    )

    assert base.receipt_digest != different_scope.receipt_digest
    assert base.receipt_digest != different_risk.receipt_digest
    assert different_revision.receipt_digest != bound_revision.receipt_digest
    evidence = EngineeringDesignGuard.as_evidence(base)
    assert evidence["changed_paths"] == ["README.md", "VERSION"]
    assert evidence["risk"] == "medium"
    assert evidence["code_revision"] == ""


def test_principle_evidence_record_covers_all_families_and_binds_revision():
    import hashlib
    import json

    receipt = EngineeringDesignGuard.review(
        intent="Update project metadata",
        changed_paths=["README.md", "VERSION"],
        risk="medium",
        code_revision="commit-abc123",
        design={"security": "not_applicable: documentation-only change"},
    )
    record = EngineeringDesignGuard.as_principle_evidence_record(
        receipt, design={"security": "not_applicable: documentation-only change"}
    )
    assert record["schema_version"] == "auren-engineering-principle-evidence-v1"
    assert record["code_revision"] == "commit-abc123"
    assert len(record["families"]) == 12
    assert record["families"]["security_privacy"]["status"] == "not_applicable"
    assert record["families"]["security_privacy"]["reason"] == "documentation-only change"
    assert all(
        family["status"] in {"enforced", "evidence_gated", "operator_controlled", "not_applicable"}
        for family in record["families"].values()
    )
    supplied_digest = record.pop("digest")
    expected_digest = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert supplied_digest == expected_digest


def test_unknown_risk_classification_fails_closed():
    import pytest

    with pytest.raises(ValueError, match="risk must be one of"):
        EngineeringDesignGuard.review(
            intent="Change request handling behavior",
            changed_paths=["portable/request_handler.py"],
            risk="very-high",
            design={"architecture": "reuse existing handler boundary"},
        )

def test_high_risk_external_change_requires_resilience_contract():
    receipt = EngineeringDesignGuard.review(
        intent="Add retry handling to outbound payment API",
        changed_paths=["portable/payment_client.py"],
        risk="high",
        design={"architecture": "client stays behind an outbound adapter"},
    )

    assert receipt.status == "blocked"
    assert any(
        f.dimension == DesignDimension.RESILIENCE and f.severity == FindingSeverity.BLOCKING
        for f in receipt.findings
    )


def test_high_risk_change_requires_curated_quality_evidence():
    receipt = EngineeringDesignGuard.review(
        intent="Optimize invoice lookup and add fallback behavior",
        changed_paths=["repository/invoice_repository.py"],
        risk="high",
        design={"architecture": "reuse existing invoice repository boundary"},
    )

    messages = [f.message.lower() for f in receipt.findings if f.severity == FindingSeverity.BLOCKING]
    assert any("reuse" in message for message in messages)
    assert any("regression" in message for message in messages)
    assert any("exception" in message for message in messages)
    assert any("logging" in message for message in messages)
    assert any("performance" in message for message in messages)
    assert any("usage patterns" in message for message in messages)
    assert any("minimal db/query" in message for message in messages)


def test_complete_domain_data_and_refactoring_contract_is_ready():
    receipt = EngineeringDesignGuard.review(
        intent="Refactor the billing aggregate and preserve API behavior",
        changed_paths=["domain/billing.py", "tests/test_billing.py"],
        risk="medium",
        design={
            "domain": "Billing bounded context owns invoice lifecycle invariants",
            "data": "Postgres is the source of truth; events are derived and replayable",
            "refactoring": "behavior delta is none; characterization and contract tests protect behavior",
            "behavior_delta": "none",
            "safety_net": "billing contract tests plus focused unit tests",
            "architecture": "domain stays independent of persistence and transport",
            "responsibility": "billing domain owns invoice lifecycle invariants and public boundary",
            "dependency_direction": "domain depends on abstractions only; transport and persistence depend inward on domain contracts",
            "construction": "validate boundary inputs and preserve diagnostic errors",
            "compatibility": "existing API remains backward compatible",
            "usage_compatibility": "existing callers, API shape, lifecycle ordering and usage remain unchanged",
            "complexity": "reduce caller knowledge by hiding invoice state transitions",
            "resilience": "not_applicable: no external or asynchronous dependency changes",
            "timeout_budget": "not_applicable: no runtime operation deadline or external call changes",
            "retry_budget": "not_applicable: no retryable operation or delivery behavior changes",
            "recovery": "not_applicable: no runtime dependency or failure-recovery path changes",
            "reuse": "reused existing invoice repository, validator and test fixtures; no new abstraction",
            "regression": "focused billing tests plus existing API contract suite",
            "exception_handling": "preserve existing exception types and translation boundaries; clean up resources",
            "logging": "reuse structured repository logger and correlation fields; no sensitive data added",
            "performance": "preserve query count and allocation profile; no new per-item work",
            "db_access": "reuse existing repository query path and batch existing reads",
            "query_pattern": "single existing query path; no N+1 or duplicate round trips",
            "schema_evolution": "not_applicable: no schema or persisted-format changes are proposed",
            "rollout": "not_applicable: no deployment, caller migration, or staged rollout changes are proposed",
        },
    )

    assert receipt.status == "ready"
    assert not receipt.blocking
    assert receipt.receipt_digest


def test_medium_risk_missing_quality_evidence_is_review_required():
    receipt = EngineeringDesignGuard.review(
        intent="Change request handling behavior",
        changed_paths=["portable/request_handler.py"],
        risk="medium",
        design={"architecture": "reuse existing handler boundary"},
    )

    assert receipt.status == "review_required"
    assert any(
        f.dimension == DesignDimension.CONSTRUCTION
        and f.severity == FindingSeverity.WARNING
        and "reuse" in f.message.lower()
        for f in receipt.findings
    )


def test_not_applicable_requires_a_reason_for_relevant_dimension():
    receipt = EngineeringDesignGuard.review(
        intent="Change retry behavior for an outbound client",
        changed_paths=["portable/client.py"],
        risk="medium",
        design={"resilience": "not_applicable"},
    )

    assert any(
        f.dimension == DesignDimension.RESILIENCE and f.severity == FindingSeverity.WARNING
        for f in receipt.findings
    )
