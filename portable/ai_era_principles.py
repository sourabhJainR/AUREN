"""Canonical AI-era principle registry; enforcement stays in existing AUREN gates."""
from __future__ import annotations
import hashlib
import json
from typing import Any

# id, name, meaning, existing control owner, focused test
_ROWS = [
("IPACT","Intent Before Action","Define intent, constraints, acceptance criteria and stopping conditions.","portable/engineering_design_guard.py","tests/test_engineering_design_guard.py"),
("VERA","Verification Before Acceptance","Accept results only with relevant, revision-bound evidence.","portable/execution_controller.py","tests/test_execution_controller_promotion_gate.py"),
("PACT","Permission And Control of Tools","Keep actions within granted authority and scope.","portable/decision_gate.py","tests/test_decision_gate.py"),
("INVAR","Invariant Preservation","Preserve essential correctness, security, compatibility and resource invariants.","portable/state_graph.py","tests/test_state_graph_hardening.py"),
("TRACE","Traceable Results And Claims Engineering","Trace material claims to source, revision, assumptions and evidence.","portable/agency_provenance.py","tests/test_agency_provenance.py"),
("R3","Recover, Resume, Roll Back","Make interruption, partial failure, resumption and rollback safe.","portable/runtime_recovery.py","tests/test_runtime_recovery.py"),
("RAIL","Resource-bounded Autonomous Iteration Limits","Bound time, cost, retries and resource use; stop when progress stalls.","portable/runtime_recovery.py","tests/test_runtime_recovery.py"),
("CIG","Context Integrity Guard","Use relevant, current, authoritative context and surface contradictions.","portable/memory_orchestrator.py","tests/test_memory_orchestrator.py"),
("BLAST","Bounded Limits And Scoped Trust","Contain failures and limit impact of faulty or untrusted components.","portable/secure_execution.py","tests/test_security_engineering_gate.py"),
("INDEP","Independent Evaluation Protocol","Challenge results with evidence not supplied solely by the producer.","portable/arena_acceptance.py","tests/test_independent_arena.py"),
("OWN","Ownership With Named Accountability","Assign owners to tasks, state transitions and handoffs.","portable/crew_adapters.py","tests/test_durable_crew_handoffs.py"),
("LEARN","Learning Evaluated Against Regression and Novelty","Learn from validated outcomes while preserving safety and regression guarantees.","portable/causal_capability_promotion.py","tests/test_causal_capability_promotion.py"),
("DELTA","Defensible Engineering with Limited, Targeted Alterations","Make the smallest justified change without weakening safety or completeness.","portable/engineering_design_guard.py","tests/test_engineering_design_guard.py"),
("HANDOFF","Handoff And Negotiated Definition Of Function","Define delegation scope, inputs, outputs, acceptance and failure behavior.","portable/crew_adapters.py","tests/test_durable_crew_handoffs.py"),
("DEGRADE","Designed, Efficient, Adaptive Graceful Recovery Under Degraded Environments","Preserve safe core functionality when dependencies fail; otherwise pause safely.","portable/runtime_recovery.py","tests/test_runtime_recovery.py"),
("TRUST","Treat Retrieved Untrusted Sources Thoughtfully","Separate untrusted data from instructions, permissions and verified facts.","portable/trusted_repository_context.py","tests/test_trusted_repository_context.py"),
("PROMOTE","Proof-Required Operational Maturation Of Tested Enhancements","Promote capabilities only after required evaluation and rollback checks pass.","portable/execution_controller.py","tests/test_execution_controller_promotion_gate.py"),
("ECONOMY","Engineering Cost Optimization Measured Over the Full Lifecycle, Not Yield Alone","Optimize inference, tools, CI, memory, review, rework, latency and risk together."," .ai-harness/runtime/context_broker.py".strip(),"tests/test_context_broker.py"),
("MEASURE","Metrics, Evidence, And Results Evaluated","Measure quality, recovery, cost and outcomes rather than activity alone.","portable/agency_observability.py","tests/test_agency_observability.py"),
("CONTEXT","Context Is an Engineering Dependency","Retrieve relevant, current, authoritative context before acting."," .ai-harness/runtime/context_broker.py".strip(),"tests/test_context_broker.py"),
("DETERM","Deterministic Decisions and Artifacts","Make planning, state transitions, artifact generation, and evidence reconciliation deterministic where correctness requires it; bind outputs to explicit inputs and versions.","portable/state_graph.py","tests/test_state_graph_hardening.py"),
("MINCORE","Minimal Trusted Execution Core","Keep authorization and security-critical execution in a small, auditable core; optional agents and plugins cannot expand its authority.","portable/secure_execution.py","tests/test_security_engineering_gate.py"),
("OUTCOME","Explicit Operation Outcomes","Represent success, failure, timeout, cancellation, partial completion, and unknown outcome distinctly; never convert uncertainty into success.","portable/execution_controller.py","tests/test_execution_controller_promotion_gate.py"),
("MIGSAFE","Compatible and Reversible State Evolution","Require compatibility checks, migration rehearsal, rollback or forward-fix plans, and continued readability of prior state.","portable/state_migration.py","tests/test_state_migration.py"),
("PROVEN","Verified Supply Chain and Artifacts","Pin dependencies and build inputs; verify source provenance, signatures, and artifact contents before execution or release.","portable/agency_provenance.py","tests/test_agency_provenance.py"),
("HUMANE","Honest and Actionable Operator Feedback","Expose blockers, next actions, uncertainty, and recovery choices; progress indicators must not hide failures or imply unsupported completion.","portable/agency_observability.py","tests/test_agency_observability.py"),
("ACCESS","Accessible Operational Interfaces","Treat accessibility of applicable CLI, dashboard, and operator workflows as a quality requirement; verify interfaces with appropriate checks.","dashboard/README.md","tests/test_principle_traceability.py"),
("REVERS","Reversibility Before Abstraction","Assess migration complexity, operational burden, and reversal cost before adding abstractions; prefer a smaller reversible change when it meets the need.","portable/engineering_design_guard.py","tests/test_engineering_design_guard.py"),
("LIFECYCLE","Named Ownership and Retirement","Give every agent, provider, plugin, policy, and compatibility layer a named owner, support status, and explicit retirement condition.","portable/identity_compat.py","tests/test_identity_compat.py"),
("PRIORITY","Correctness and Authority Over Optimization","Token savings, speed, autonomy, and availability never override permission, evidence, security, or correctness gates.","portable/decision_gate.py","tests/test_decision_gate.py"),
]
PRINCIPLES = tuple({"id": row[0], "name": row[1], "meaning": row[2], "owner": row[3], "test": row[4]} for row in _ROWS)
SCHEMA_VERSION = "auren-ai-era-principles-v2"
ALLOWED_RISKS = {"low", "medium", "high", "critical"}

def canonical_registry() -> dict[str, Any]:
    exception_policy = (
        "Apply only when relevant to task risk and scope; any not_applicable decision must state a "
        "specific reason. Never waive user approval, security, authority, independent verification, "
        "required promotion gates, or rollback obligations for consequential changes."
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "principles": [dict(p, exception_policy=exception_policy) for p in PRINCIPLES],
        "policy": "Traceability registry only; existing AUREN guards remain authoritative.",
    }

def registry_digest() -> str:
    raw = json.dumps(canonical_registry(), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()

def build_contract_receipt(intent: str, changed_paths: list[str] | tuple[str, ...], risk: str, code_revision: str = "") -> dict[str, Any]:
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError("AI-era principle receipt requires non-empty intent")
    normalized_risk = str(risk).strip().lower()
    if normalized_risk not in ALLOWED_RISKS:
        raise ValueError(f"risk must be one of: {', '.join(sorted(ALLOWED_RISKS))}")
    paths = sorted({str(p).replace("\\", "/").strip("/") for p in changed_paths if str(p).strip()})
    payload = {"schema_version": "auren-ai-era-principle-receipt-v2",
        "intent_digest": hashlib.sha256(intent.strip().encode()).hexdigest(),
        "changed_paths": paths, "risk": normalized_risk, "code_revision": str(code_revision or ""),
        "registry_digest": registry_digest(), "principles": [p["id"] for p in PRINCIPLES],
        "selection_policy": "Applicability and blocking status remain decided by the canonical EngineeringDesignGuard and existing security, verification, recovery and promotion gates."}
    payload["digest"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return payload
