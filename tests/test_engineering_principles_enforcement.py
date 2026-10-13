"""Regression tests for risk-aware engineering principle enforcement."""

from portable.engineering_design_guard import (
    DesignDimension,
    EngineeringDesignGuard,
    FindingSeverity,
)


def _messages(receipt, severity=None):
    return [f.message.lower() for f in receipt.findings if severity is None or f.severity == severity]



def test_architecture_contract_requires_owner_and_dependency_direction():
    receipt = EngineeringDesignGuard.review(
        intent="Refactor module architecture and dependency boundaries",
        changed_paths=["portable/service.py"],
        risk="high",
        design={"architecture": "Use a clean architecture"},
    )
    blocking = _messages(receipt, FindingSeverity.BLOCKING)
    assert receipt.status == "blocked"
    assert any("responsible module/owner" in message for message in blocking)
    assert any("dependency direction" in message for message in blocking)



def test_resilience_contract_requires_timeout_retry_budget_and_recovery():
    receipt = EngineeringDesignGuard.review(
        intent="Add resilient retry behavior to outbound service API",
        changed_paths=["portable/client.py"],
        risk="high",
        design={"resilience": "Handle failures safely"},
    )
    blocking = _messages(receipt, FindingSeverity.BLOCKING)
    assert receipt.status == "blocked"
    assert any("deadline and timeout budget" in message for message in blocking)
    assert any("bounded retry budget" in message for message in blocking)
    assert any("failure containment and recovery" in message for message in blocking)


def test_resilience_contract_accepts_explicit_bounded_controls():
    receipt = EngineeringDesignGuard.review(
        intent="Add resilient retry behavior to outbound service API",
        changed_paths=["portable/client.py"],
        risk="high",
        design={
            "resilience": "Remote dependency uses bounded failure handling",
            "timeout_budget": "5-second total deadline propagated through nested calls",
            "retry_budget": "at most two retries with exponential backoff and jitter",
            "recovery": "circuit breaker, bounded concurrency bulkhead, and readiness probe",
            "architecture": "client remains behind the outbound adapter",
            "responsibility": "client adapter owns remote-call policy",
            "dependency_direction": "application depends on client interface, implementation points outward",
            "threat_model": "no credentials are passed to untrusted response handlers",
            "authorization": "least-privilege service identity",
            "performance_budget": "p95 latency and max concurrency are measured",
            "reuse": "reuse existing HTTP client",
            "regression": "timeout, retry, and breaker fault-injection tests",
            "exception_handling": "preserve typed errors and close resources",
            "logging": "record correlation IDs without request secrets",
            "usage_compatibility": "preserve existing API and error contract",
            "rollout": "not_applicable: no deployment or staged migration change is proposed",
        },
    )
    assert not any(
        phrase in message
        for phrase in ("deadline and timeout budget", "bounded retry budget", "failure containment and recovery")
        for message in _messages(receipt, FindingSeverity.BLOCKING)
    )



def test_data_integrity_contract_requires_schema_evolution_decision():
    receipt = EngineeringDesignGuard.review(
        intent="Change persistent storage and data integrity handling",
        changed_paths=["portable/storage/repository.py"],
        risk="high",
        design={
            "source_of_truth": "database remains authoritative",
            "consistency": "writes commit transactionally",
        },
    )
    blocking = _messages(receipt, FindingSeverity.BLOCKING)
    assert receipt.status == "blocked"
    assert any("schema/format evolution" in message for message in blocking)


def test_data_integrity_contract_accepts_explicit_schema_non_applicability():
    receipt = EngineeringDesignGuard.review(
        intent="Change persistent storage and data integrity handling",
        changed_paths=["portable/storage/repository.py"],
        risk="high",
        design={
            "source_of_truth": "database remains authoritative",
            "consistency": "writes commit transactionally",
            "query_pattern": "reuse bounded batch reads and avoid N+1 round trips",
            "schema_evolution": "not_applicable: schema and persisted format remain unchanged",
            "architecture": "repository remains behind a stable storage boundary",
            "responsibility": "storage adapter owns persistence invariants",
            "dependency_direction": "application depends on storage interface only",
            "resilience": "storage operation is bounded and recoverable",
            "timeout_budget": "5-second database operation deadline",
            "retry_budget": "no retries for non-idempotent transaction failures",
            "recovery": "rollback transaction on failed write",
            "reuse": "reuse existing transaction manager",
            "regression": "transaction rollback and data integrity tests",
            "exception_handling": "rollback and preserve typed storage errors",
            "logging": "record correlation ID without row contents",
            "performance": "query count remains bounded",
            "usage_compatibility": "repository contract and caller behavior remain stable",
            "rollout": "not_applicable: no deployment or staged migration change is proposed",
        },
    )
    assert not any(
        "schema/format evolution" in message
        for message in _messages(receipt, FindingSeverity.BLOCKING)
    )



def test_compatibility_contract_requires_caller_and_rollout_decisions():
    receipt = EngineeringDesignGuard.review(
        intent="Change a public API contract and migration behavior",
        changed_paths=["portable/api/client.py"],
        risk="high",
        design={"compatibility": "Keep compatibility in mind"},
    )
    blocking = _messages(receipt, FindingSeverity.BLOCKING)
    assert receipt.status == "blocked"
    assert any("existing callers" in message for message in blocking)
    assert any("migration/rollout/rollback" in message for message in blocking)


def test_legacy_contract_requires_characterization_and_safe_seam():
    receipt = EngineeringDesignGuard.review(
        intent="Replace a legacy adapter and migrate callers",
        changed_paths=["portable/legacy/adapter.py"],
        risk="high",
        design={"legacy": "Replace old implementation"},
    )
    blocking = _messages(receipt, FindingSeverity.BLOCKING)
    assert receipt.status == "blocked"
    assert any("characterization/regression" in message for message in blocking)
    assert any("seam/dependency break" in message for message in blocking)


def test_security_is_inferred_and_high_risk_missing_contract_blocks():
    receipt = EngineeringDesignGuard.review(
        intent="Change authentication and authorization for external tool execution",
        changed_paths=["portable/security/tool_runner.py"],
        risk="high",
        design={},
    )
    assert DesignDimension.SECURITY.value in receipt.dimensions
    assert receipt.status == "blocked"
    assert any("trust boundary" in message for message in _messages(receipt, FindingSeverity.BLOCKING))
    assert any("authorization" in message for message in _messages(receipt, FindingSeverity.BLOCKING))


def test_security_contract_requires_authority_boundary_not_only_security_label():
    receipt = EngineeringDesignGuard.review(
        intent="Update security checks for user-controlled input",
        changed_paths=["portable/validator.py"],
        risk="high",
        design={"security": "Validate input at the trust boundary and fail closed"},
    )
    assert any("least-privilege" in message for message in _messages(receipt, FindingSeverity.BLOCKING))


def test_performance_contract_accepts_measurable_budget():
    receipt = EngineeringDesignGuard.review(
        intent="Optimize hot-path latency and memory use",
        changed_paths=["portable/performance/runner.py"],
        risk="high",
        design={
            "performance": "p95 latency must not regress; memory remains bounded",
            "performance_budget": "p95 <= existing baseline; benchmark before and after",
            "architecture": "reuse current runner",
            "resilience": "bounded work and explicit timeout",
            "reuse": "reused existing runner",
            "regression": "benchmark plus regression suite",
            "exception_handling": "preserve current exception behavior",
            "logging": "reuse structured logging",
            "usage_compatibility": "existing API unchanged",
        },
    )
    assert DesignDimension.PERFORMANCE.value in receipt.dimensions
    assert not any("measurable performance" in message for message in _messages(receipt))


def test_concurrency_requires_ownership_and_race_safety_contract():
    receipt = EngineeringDesignGuard.review(
        intent="Introduce parallel workers and cancellation for shared task state",
        changed_paths=["portable/concurrency/worker.py"],
        risk="high",
        design={"concurrency": "workers own isolated task state"},
    )
    assert DesignDimension.CONCURRENCY.value in receipt.dimensions
    assert receipt.status == "blocked"
    assert any("cancellation/synchronization" in message for message in _messages(receipt, FindingSeverity.BLOCKING))


def test_distributed_contract_requires_consistency_and_duplicate_handling():
    receipt = EngineeringDesignGuard.review(
        intent="Design distributed cache replication with CAP and PACELC trade-offs",
        changed_paths=["portable/distributed/replica.py"],
        risk="high",
        design={"distributed": "replicated cache across nodes"},
    )
    assert DesignDimension.DISTRIBUTED.value in receipt.dimensions
    assert receipt.status == "blocked"
    assert any("consistency and cap/pacelc" in message for message in _messages(receipt, FindingSeverity.BLOCKING))
    assert any("duplicate delivery" in message for message in _messages(receipt, FindingSeverity.BLOCKING))


def test_distributed_contract_can_record_consistency_and_delivery_semantics():
    receipt = EngineeringDesignGuard.review(
        intent="Design distributed queue consumer with retry and partition handling",
        changed_paths=["portable/distributed/consumer.py"],
        risk="high",
        design={
            "distributed": "consumer across multiple nodes",
            "consistency": "eventual consistency; tolerate stale reads during partition",
            "idempotency": "deduplicate by stable message id",
            "query_pattern": "bounded batch reads; no N+1 query path",
            "architecture": "consumer uses existing adapter",
            "resilience": "timeouts and bounded retries with jitter",
            "reuse": "reuse current queue client",
            "regression": "contract and replay tests",
            "exception_handling": "propagate terminal errors and release resources",
            "logging": "correlation id only; no payload secrets",
            "performance": "bounded batch size and queue depth",
            "usage_compatibility": "existing event schema preserved",
        },
    )
    assert not any("consistency and cap/pacelc" in message for message in _messages(receipt))
    assert not any("duplicate delivery" in message for message in _messages(receipt))


def test_data_correctness_contract_requires_invariant_or_transaction_semantics():
    receipt = EngineeringDesignGuard.review(
        intent="Change transaction boundaries and data integrity checks",
        changed_paths=["portable/storage/transaction.py"],
        risk="high",
        design={"data": "Postgres stores authoritative state"},
    )
    assert DesignDimension.CORRECTNESS.value in receipt.dimensions
    assert any("critical invariants" in message for message in _messages(receipt, FindingSeverity.BLOCKING))


def test_ai_agent_contract_requires_authority_and_evaluation():
    receipt = EngineeringDesignGuard.review(
        intent="Add autonomous agent tool execution and model evaluation",
        changed_paths=["portable/agents/tool_runner.py"],
        risk="high",
        design={"evaluation": "offline tests exist"},
    )
    assert DesignDimension.AI_AGENT.value in receipt.dimensions
    assert receipt.status == "blocked"
    assert any("agent authority" in message for message in _messages(receipt, FindingSeverity.BLOCKING))


def test_reasoned_not_applicable_is_allowed_for_concurrency_contract():
    receipt = EngineeringDesignGuard.review(
        intent="Document synchronous configuration format; no shared state or parallel execution",
        changed_paths=["docs/configuration.md"],
        risk="medium",
        design={
            "concurrency": "not_applicable: change only documents a synchronous config format",
            "ownership": "not_applicable: no mutable runtime state is changed",
            "synchronization": "not_applicable: no concurrent execution is introduced",
        },
    )
    assert not any("shared-state ownership" in message for message in _messages(receipt))
    assert not any("cancellation/synchronization" in message for message in _messages(receipt))

# Keep the suite runnable by the repository's unittest-based CI entrypoint.
import unittest


class EngineeringPrincipleEnforcementTests(unittest.TestCase):
    def test_resilience_contract_requires_timeout_retry_and_recovery_case(self):
        test_resilience_contract_requires_timeout_retry_budget_and_recovery()

    def test_resilience_contract_accepts_bounded_controls_case(self):
        test_resilience_contract_accepts_explicit_bounded_controls()

    def test_data_integrity_requires_schema_evolution_decision_case(self):
        test_data_integrity_contract_requires_schema_evolution_decision()

    def test_data_integrity_accepts_schema_non_applicability_case(self):
        test_data_integrity_contract_accepts_explicit_schema_non_applicability()

    def test_compatibility_requires_caller_and_rollout_decisions_case(self):
        test_compatibility_contract_requires_caller_and_rollout_decisions()

    def test_legacy_requires_characterization_and_safe_seam_case(self):
        test_legacy_contract_requires_characterization_and_safe_seam()

    def test_security_is_inferred_and_high_risk_missing_contract_blocks_case(self):
        test_security_is_inferred_and_high_risk_missing_contract_blocks()

    def test_security_contract_requires_authority_boundary_case(self):
        test_security_contract_requires_authority_boundary_not_only_security_label()

    def test_performance_contract_accepts_measurable_budget_case(self):
        test_performance_contract_accepts_measurable_budget()

    def test_concurrency_requires_ownership_and_race_safety_case(self):
        test_concurrency_requires_ownership_and_race_safety_contract()

    def test_distributed_contract_requires_consistency_and_duplicate_handling_case(self):
        test_distributed_contract_requires_consistency_and_duplicate_handling()

    def test_distributed_contract_accepts_consistency_and_delivery_case(self):
        test_distributed_contract_can_record_consistency_and_delivery_semantics()

    def test_data_correctness_requires_invariant_case(self):
        test_data_correctness_contract_requires_invariant_or_transaction_semantics()

    def test_ai_agent_requires_authority_and_evaluation_case(self):
        test_ai_agent_contract_requires_authority_and_evaluation()

    def test_reasoned_not_applicable_case(self):
        test_reasoned_not_applicable_is_allowed_for_concurrency_contract()
