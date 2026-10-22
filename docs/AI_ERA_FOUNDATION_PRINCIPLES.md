# AUREN AI-Era Foundation Principles

This is the canonical human-readable companion to `portable/ai_era_principles.py`. It extends SOLID, DRY, YAGNI, KISS, GRASP, and AUREN's existing 12-family engineering taxonomy; it does not replace the existing deterministic EngineeringDesignGuard or create a parallel policy engine.

## Foundation principles

| ID | Principle | Required behavior |
|---|---|---|
| IPACT | Intent Before Action | Bind work to explicit intent, constraints, acceptance criteria, and stopping conditions. |
| VERA | Verification Before Acceptance | Accept outcomes only with relevant, revision-bound verification evidence. |
| PACT | Permission And Control of Tools | Keep consequential actions within granted authority and scope. |
| INVAR | Invariant Preservation | Preserve correctness, security, compatibility, and resource invariants. |
| TRACE | Traceable Results And Claims Engineering | Trace material claims and outcomes to sources, revisions, assumptions, and evidence. |
| R3 | Recover, Resume, Roll Back | Make interruption, partial failure, resumption, and rollback safe and testable. |
| RAIL | Resource-bounded Autonomous Iteration Limits | Bound time, cost, retries, and resource use; stop or change approach when progress stalls. |
| CIG | Context Integrity Guard | Use relevant, current, authoritative context; surface stale or conflicting inputs. |
| BLAST | Bounded Limits And Scoped Trust | Contain failures and limit the impact of faulty or untrusted components. |
| INDEP | Independent Evaluation Protocol | Challenge important results with evidence not supplied solely by the producer. |
| OWN | Ownership With Named Accountability | Assign accountable owners to tasks, state transitions, and handoffs. |
| LEARN | Learning Evaluated Against Regression and Novelty | Learn from validated outcomes and preserve regression and safety guarantees. |
| DELTA | Defensible Engineering with Limited, Targeted Alterations | Make the smallest justified change that meets the requirement without weakening safety or completeness. |
| HANDOFF | Handoff And Negotiated Definition Of Function | Define delegation scope, inputs, outputs, acceptance criteria, and failure behavior. |
| DEGRADE | Designed, Efficient, Adaptive Graceful Recovery Under Degraded Environments | Preserve safe core functionality when dependencies fail; otherwise pause safely. |
| TRUST | Treat Retrieved Untrusted Sources Thoughtfully | Separate untrusted data from instructions, permissions, and verified facts. |
| PROMOTE | Proof-Required Operational Maturation Of Tested Enhancements | Promote capabilities only after required evaluation and rollback readiness checks pass. |
| ECONOMY | Engineering Cost Optimization Measured Over the Full Lifecycle, Not Yield Alone | Optimize inference, tools, CI, memory, review, rework, latency, and operational risk together. |
| MEASURE | Metrics, Evidence, And Results Evaluated | Measure outcomes, quality, recovery, and cost rather than activity alone. |
| CONTEXT | Context Is an Engineering Dependency | Retrieve relevant, current, authoritative context before generation or consequential action. |
| DETERM | Deterministic Decisions and Artifacts | Make planning, state transitions, artifact generation, and evidence reconciliation deterministic where correctness requires it. |
| MINCORE | Minimal Trusted Execution Core | Keep authorization and security-critical execution small and auditable; optional agents and plugins cannot expand authority. |
| OUTCOME | Explicit Operation Outcomes | Distinguish success, failure, timeout, cancellation, partial completion, and unknown outcome. |
| MIGSAFE | Compatible and Reversible State Evolution | Require compatibility checks, migration rehearsal, rollback or forward-fix plans, and old-state readability. |
| PROVEN | Verified Supply Chain and Artifacts | Pin dependencies and build inputs; verify provenance, signatures, and artifact contents before execution or release. |
| HUMANE | Honest and Actionable Operator Feedback | Show blockers, next actions, uncertainty, and recovery choices without hiding failures behind progress indicators. |
| ACCESS | Accessible Operational Interfaces | Treat accessible CLI, dashboard, and operational workflows as quality requirements where applicable. |
| REVERS | Reversibility Before Abstraction | Assess migration complexity, operational burden, and reversal cost before adding abstractions. |
| LIFECYCLE | Named Ownership and Retirement | Give each agent, provider, plugin, policy, and compatibility layer an owner, support status, and retirement condition. |
| PRIORITY | Correctness and Authority Over Optimization | Token savings, speed, autonomy, and availability cannot override permission, evidence, security, or correctness gates. |

## Enforcement model

- **Architecture and agent contracts:** every non-trivial task passes through the existing `EngineeringDesignGuard`; its declared design dimensions and review receipt remain the canonical pre-execution contract.
- **StateGraph and orchestration:** the runtime records a digest-bound AI-era principle registry receipt before any graph node executes. The receipt binds intent, changed paths, risk, code revision, and the versioned registry.
- **Memory and context:** CIG, CONTEXT, TRACE, TRUST, and LEARN map to the existing context broker, canonical memory orchestrator, provenance records, and guarded learning controls. Memory is not a source of authority by itself.
- **Verification and promotion:** VERA, INDEP, and PROMOTE map to existing verification, independent evaluation, decision, promotion, and rollback gates. A registry entry or receipt alone is not proof that a capability is safe.
- **Recovery and autonomy:** R3, RAIL, DEGRADE, BLAST, and HANDOFF map to existing runtime budgets, recovery, sandbox, execution-controller, and durable handoff mechanisms.
- **CI and regression:** focused tests validate the 30-entry registry, owner/test references, receipt digest, and pre-node orchestration recording. Architecture-integrity and repository test workflows validate the changed revision.
- **Exceptions:** task applicability remains risk-based and is decided by the canonical guard. Any `not_applicable` declaration must include a reason. Security, authority, user approval, and promotion gates cannot be waived by a principle receipt.
- **Evidence limits:** repository CI can validate code and contracts; it cannot substitute for an independently operated Arena campaign, external evaluator attestation, or operator-run production rollback rehearsal.

Principle registry status is traceability, not a claim that every listed control is fully enforced in every deployment. Existing owner modules and focused tests are the source of behavioral enforcement evidence.
