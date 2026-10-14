# Engineering Principle Traceability and Deployment Decisions

This matrix is intentionally conservative. A family is not called fully enforced merely because it appears in the catalogue. The machine-readable counterpart is `docs/engineering-principle-traceability.json`; `tests/test_principle_traceability.py` checks that all twelve canonical families have a status, owner code, and focused test reference.

Status meanings:

- **enforced** — a deterministic runtime guard rejects the prohibited state.
- **evidence-gated** — a guard exists, but acceptance still depends on revision-bound evidence or a configured reviewer.
- **operator-controlled** — a deployment/provider/evaluator choice cannot be proven by repository CI.
- **not_applicable** — justified per task/deployment with a reason; never used as a blanket family exemption.

## Twelve-family owner map

| Principle family | Canonical owner | Focused evidence/tests | Current status | Material remaining gap |
|---|---|---|---|---|
| Fundamental design | `portable/engineering_design_guard.py`, `portable/orchestration.py` | `tests/test_engineering_design_guard.py`, `tests/test_orchestration_engineering_guard.py` | evidence-gated | The guard classifies and records evidence; an independent reviewer must still verify task-specific behavior. |
| Security and privacy | `portable/decision_gate.py`, `portable/execution_controller.py` | `tests/test_execution_controller_promotion_gate.py` | evidence-gated | Real host tool permissions, approval identities, and secrets boundaries require deployment configuration and rehearsal. |
| Performance and cost | `.ai-harness/runtime/graph_agent_team.py`, `.ai-harness/engine.py` | `tests/test_graph_agent_team.py`, `tests/test_context_broker.py` | operator-controlled | Resource bounds exist, but universal p95/p99 latency and cost SLOs are not enforced across every workflow. |
| Concurrency | `portable/crew_adapters.py`, `portable/state_graph.py` | `tests/test_crew_adapters.py`, `tests/test_graph_agent_team_crew_dispatch.py` | evidence-gated | The durable execution controller fences stale task finalization with lease generations in its local SQLite ownership model; multi-node consensus/ownership guarantees are not claimed. |
| Distributed systems | `portable/state_graph.py`, `portable/crew_adapters.py`, `portable/state_paths.py` | `tests/test_crew_adapters.py`, `tests/test_state_migration.py` | operator-controlled | Distributed quorum/consensus, transactional outbox/inbox, and saga compensation are not claimed for the current local file-backed runtime. |
| Reliability and recovery | `.ai-harness/engine.py`, `portable/crew_adapters.py`, `portable/state_migration.py` | `tests/test_crew_adapters.py`, `tests/test_state_migration.py`, `tests/test_graph_agent_team_crew_dispatch.py` | evidence-gated | Cooperative cancellation of non-cooperative external tools, measured recovery objectives, fault-injection/chaos coverage, and operator rollback rehearsals remain incomplete. |
| Data correctness | `portable/state_paths.py`, `portable/state_migration.py`, `portable/engineering_design_guard.py` | `tests/test_state_migration.py`, `tests/test_engineering_design_guard.py` | evidence-gated | File-backed stores do not provide cross-store ACID transactions; migration rollback still needs deployment rehearsal. |
| API and integration | `portable/identity_compat.py`, `portable/state_graph.py`, `portable/crew_adapters.py` | `tests/test_identity_compat.py`, `tests/test_crew_adapters.py` | evidence-gated | Real provider/tool adapters are operator registered; the full legacy alias inventory and retirement plan remain open. |
| Testing and verification | `portable/orchestration.py`, `portable/engineering_design_guard.py`, `.ai-harness/engine.py` | `tests/test_orchestration_engineering_guard.py`, `tests/test_engineering_design_guard.py` | evidence-gated | Independent evaluator provenance and external holdout isolation cannot be established by repository CI alone. |
| Delivery and operations | `portable/execution_controller.py`, `portable/decision_gate.py`, `.ai-harness/engine.py` | `tests/test_execution_controller_promotion_gate.py` | evidence-gated | Operator deployment, last-known-good publication, and rollback rehearsals require external evidence. |
| Observability and governance | `.ai-harness/runtime/context_broker.py`, `.ai-harness/engine.py` | `tests/test_context_broker.py`, `tests/test_contract_alignment.py` | evidence-gated | Liveness/readiness separation and universal SLO/error-budget enforcement remain incomplete. |
| AI and autonomous systems | `.ai-harness/runtime/graph_agent_team.py`, `portable/causal_capability_promotion.py` | `tests/test_graph_agent_team.py`, `tests/test_graph_agent_team_crew_dispatch.py` | operator-controlled | Independent Arena campaigns, sealed holdouts, external oracle/evaluator receipts, and cross-domain transfer evidence require external infrastructure. |

## CAP/PACELC and consistency decisions by real boundary

CAP does not offer three independently selectable guarantees. In a distributed system, a partition forces a choice between consistency and availability for the affected operation. PACELC additionally asks whether the system chooses latency or consistency when no partition is occurring. The current default deployment is local/file-backed; distributed guarantees must not be inferred from local checkpoint tests.

| Boundary | Current consistency/ownership model | CAP/PACELC decision | Explicit limitation |
|---|---|---|---|
| Episodic, semantic, and engineering memory | Local file-backed state, with atomic replacement/checkpoint digests where implemented; one writer is the supported default. | CAP is not applicable until the state is shared across nodes. If it becomes shared, authoritative memory updates should prefer consistency over availability during a partition. | No replicated-memory consensus or cross-file ACID transaction is provided by the current local runtime. |
| Task ownership and supervisor checkpoints | One authoritative supervisor writes a task checkpoint; run, intent, plan, schema, and digest bindings reject stale state. | A distributed deployment must use leases plus monotonically increasing fencing generations. During a partition, stale-owner writes must be rejected, even if that reduces availability. | Current checkpoint digests are integrity bindings, not a lease or fencing token. Do not run competing supervisors against the same state root. |
| Crew coordination and handoffs | Bounded durable local queue, idempotency IDs, shared retry budget, and persisted independent-review receipts. | At-least-once replay is handled at the orchestration boundary; authoritative completion is consistency-first. No transport-level exactly-once effect is claimed. | A multi-node queue needs a transactional outbox/inbox or equivalent, plus external-effect idempotency and compensation. |
| Evidence and promotion | Canonical digest-bound receipts tied to intent and a tested workspace revision; consequential promotion requires verified evidence and an authorized decision record. | Evidence and promotion are consistency-first. Conflicting or stale evidence blocks promotion instead of choosing availability. | The local runtime does not provide replicated quorum storage; external evidence authenticity remains a separate trust boundary. |
| Remote tools and provider APIs | Adapter/provider controls retries, timeouts, and effect semantics; proposal dispatch itself does not grant side-effect authority. | PACELC is provider-specific. For authoritative writes, prefer consistency and idempotency over lower latency; advisory telemetry may be eventual only if explicitly designed that way. | Exactly-once remote effects cannot be assumed. Use per-operation idempotency keys and explicit saga compensation where multi-step effects are irreversible. |

## Cross-cutting gaps that remain open

- One deadline and cancellation budget shared by supervisor, graph nodes, crews, and remote tools.
- Fencing/lease semantics for concurrent supervisors and stale-owner writes.
- Transactional outbox/inbox and saga compensation for external side effects where required.
- Measured performance budgets and tail-latency gates for critical workflows.
- Liveness/readiness separation, recovery objectives, SLO/error-budget enforcement, and fault-injection campaigns.
- Independent evaluator/oracle, sealed holdout corpus, isolation audit, and operator-level publication/migration/rollback evidence.

These are explicit implementation or operator gates, not claims that the architecture already provides distributed database semantics or independent Arena acceptance.
