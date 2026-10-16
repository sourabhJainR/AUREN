# AUREN Root Engineering Principles Contract

This is the compact root contract for all assigned work in AUREN. It is applied through `AGENTS.md`, the AI Coding Orchestrator, `.ai-harness/ENGINEERING_DESIGN_POLICY.md`, `portable/engineering_design_guard.py`, and the existing task/evidence/verification lifecycle. It is not a second policy engine.

See the [12-family enforcement traceability matrix](docs/ENGINEERING_PRINCIPLE_TRACEABILITY.md) and its [machine-readable counterpart](docs/engineering-principle-traceability.json) for code owners, focused tests, remaining gaps, and per-boundary CAP/PACELC decisions.

## Non-negotiable operating rules

1. Start from the task intent, acceptance criteria, constraints, non-goals, affected boundaries, and stopping condition.
2. Inspect the repository and search for existing implementations before adding abstractions, dependencies, patterns, or parallel infrastructure.
3. Select principles by task shape and risk. Apply the smallest useful set; do not turn principles into rituals or use patterns without a concrete reason.
4. Record material design choices and evidence in canonical task context. For each important lens, provide evidence or `not_applicable: reason`; never silently omit it.
5. Preserve security, privacy, authorization, compatibility, existing contracts, and user approval boundaries. User and repository requirements outrank style preferences; safety and permission boundaries cannot be waived by a task plan.
6. Verify outcomes with the strongest relevant evidence: tests, build/type/lint/static checks, contract tests, security checks, performance evidence, source citations, independent review, or production/rollout signals.
7. Re-evaluate after fixes on the exact final commit. Missing, stale, queued, skipped, or failed evidence is not a pass. Use bounded retries, change approach when a repeated hypothesis fails, and preserve the last verified state and rollback path.
8. Learn from verified outcomes only. Never silently modify permanent rules, credentials, permissions, approval gates, or safety controls.

## Canonical 12-family taxonomy

Use these 12 families to classify task risk and select detailed principles. Security, correctness, testing, observability, and recovery remain cross-cutting requirements whenever relevant.

| # | Family | Representative principles | Apply when |
|---:|---|---|---|
| 01 | Fundamental design | SOLID, DRY, KISS, YAGNI, GRASP, DDD, cohesion/coupling, composition, design patterns | Every change; choose the smallest design that fits the real problem |
| 02 | Security and privacy | Zero Trust, threat modeling, least privilege, complete mediation, privacy by design, defense in depth, supply-chain security | Every trust boundary; increase assurance for sensitive or externally reachable paths |
| 03 | Performance and cost | Complexity, profiling, capacity, resource/cost budgets, batching, caching, load shedding, tail latency | Material CPU, memory, I/O, throughput, latency, or cost impact |
| 04 | Concurrency | Race freedom, ownership, synchronization, structured concurrency, cancellation, deadlock prevention, bounded queues | Parallel/asynchronous execution or shared mutable state |
| 05 | Distributed systems | CAP, PACELC, consistency, quorum, consensus, split-brain prevention, idempotency, delivery semantics, sagas, outbox/inbox | Network, multi-process, queue, replica, remote tool, or partial-failure behavior |
| 06 | Reliability and recovery | Fault containment, graceful degradation, timeout budgets, retries with backoff/jitter, health/readiness, checkpoints, RTO/RPO, SLOs, chaos tests | Any dependency, persistent task, background worker, deployment, or recoverable workflow |
| 07 | Data and correctness | ACID where supported, invariants, pre/postconditions, referential integrity, optimistic concurrency, schema evolution, provenance, reconciliation | Persistent state, transactions, evidence, migrations, or correctness-sensitive transformations |
| 08 | API and integration | Explicit contracts, versioning, compatibility, consumer-driven contracts, ports/adapters, bounded dependencies, anti-corruption layers | Public/internal interfaces, providers, plugins, adapters, or service boundaries |
| 09 | Testing and verification | Test pyramid, property/fuzz/mutation/differential/contract testing, fault injection, independent holdouts, rollback verification | Every behavior change; select techniques based on risk and failure modes |
| 10 | Delivery and operations | CI/CD, reproducible builds, artifact provenance, feature flags, safe rollout, migration safety, rollback/forward recovery | Build, packaging, release, infrastructure, schema, or deployment changes |
| 11 | Observability and governance | Structured logs, metrics, traces, audit trails, SLIs/SLOs, error budgets, evidence integrity, decision records | Runtime behavior, autonomous actions, production operation, or review/promotion decisions |
| 12 | AI and autonomous systems | Authority before action, least-privilege tools, human approval, uncertainty, bounded autonomy, safe learning, memory provenance, independent transfer, promotion/rollback | Model, agent, memory, tool use, learning, or autonomous execution |

## Extended reference inventory

The catalogue below preserves the broader engineering concepts available for task-time selection. It is a reference, not a requirement to use every item.

### Object-oriented concepts

Class/object, interface/abstract class, inheritance, encapsulation, abstraction, polymorphism, overloading/overriding, association/aggregation/composition/dependency, access modifiers, static/instance members, generics, delegates/events, extension methods, records/value objects, equality/identity, lifecycle/disposal, covariance/contravariance, reflection/attributes, pattern matching, mutable/immutable state.

### Design patterns

- **Creational:** Factory Method, Abstract Factory, Builder, Prototype, Singleton.
- **Structural:** Adapter, Bridge, Composite, Decorator, Facade, Flyweight, Proxy.
- **Behavioral:** Chain of Responsibility, Command, Interpreter, Iterator, Mediator, Memento, Observer, State, Strategy, Template Method, Visitor.
- **Application/architectural:** Dependency Injection, Repository, Unit of Work, Specification, Options, Null Object, Object Pool, Service Layer, DTO, MVC, MVVM, Front Controller, Plugin Architecture, Hexagonal Architecture. Select only when the design forces justify the added structure.

### Architecture styles and DDD

Layered, Clean, Onion, Hexagonal/Ports-and-Adapters, Modular Monolith, Microservices, SOA, Event-Driven, Serverless, Microkernel/Plugins, Pipe-and-Filter, Peer-to-Peer, Client-Server, Space-Based, CQRS, Event Sourcing, Data-Centric architecture. DDD concepts: Ubiquitous Language, Domain Model, Entity, Value Object, Aggregate/Aggregate Root, Repository, Domain/Application Service, Domain Event, Bounded Context, Context Mapping, Anti-Corruption Layer, Shared Kernel, Customer-Supplier, Open Host Service, Published Language, Factory, invariant enforcement, strategic/tactical DDD. Avoid domain ceremony for simple CRUD.

### API, integration, and distributed systems

REST/resource-oriented design, statelessness, idempotency, API versioning/backward compatibility, consumer-driven contracts, OpenAPI, pagination/filtering/sorting, rate limits, retry/backoff, timeout/deadline propagation, circuit breaker, bulkhead, correlation IDs, distributed tracing, API Gateway, BFF, webhooks, message-based integration, schema evolution, graceful degradation. Distributed concepts: CAP/PACELC, strong/eventual/read-your-writes consistency, quorum, leader election, replication, sharding/partitioning, load balancing, service discovery, health checks, Saga, transactional outbox, inbox/deduplication, idempotent consumers, dead-letter queues, compensating transactions, backpressure, queue-based load leveling, graceful shutdown, split-brain prevention, and exactly-once processing limitations.

### Data and database engineering

Normalization/denormalization, ACID/BASE trade-offs, referential integrity, primary/foreign keys, indexing/query optimization, optimistic/pessimistic concurrency, transaction isolation, deadlock handling, connection pooling, database-per-service, schema/zero-downtime migrations, CQRS read models, materialized views, CDC, partitioning, retention/archival, lineage/quality validation, polyglot persistence, cache invalidation, multi-tenant isolation, soft/hard delete.

### Security and privacy

Zero Trust, defense in depth, least privilege, secure-by-default, fail securely, authentication/authorization separation, RBAC/ABAC, separation of duties, threat modeling, input validation/output encoding, parameterized queries, secrets management, encryption in transit/at rest, key rotation, secure sessions, audit logging, SBOM/supply-chain controls, dependency scanning, complete mediation, secure error handling, data minimization, privacy by design, security testing.

### Testing and code quality

TDD, BDD, ATDD, unit/integration/contract/component/E2E/regression/smoke/sanity/exploratory testing, property-based, mutation, fuzz, differential, snapshot, golden-master, boundary-value, equivalence partitioning, pairwise, risk-based testing, test pyramid/isolation, mocks/stubs/fakes/spies, coverage, performance/load/chaos/accessibility testing. Code practices: readable names, focused methods, formatting, explicit errors, strong types, avoid primitive obsession/magic numbers/global state/unnecessary side effects/boolean mode flags, deterministic functions where useful, no premature optimization, continuous refactoring, static analysis, linting, code review, dependency hygiene, dead-code removal, make invalid states hard to represent, document why.

### Concurrency, performance, and operations

Thread safety, shared-state immutability, synchronization/mutual exclusion, locks/semaphores, atomic operations, lock-free/wait-free algorithms, producer-consumer, task-based async, structured concurrency, cancellation/timeouts, deadlock/race prevention, bounded queues, backpressure, concurrency versus parallelism, thread-pool/resource ownership, async/await. Performance: Big-O, measure before optimizing, cache-aside/write-through/write-behind, lazy/eager loading, batching, streaming/pagination, horizontal/vertical scaling, load shedding, pooling, allocation control, N+1 prevention, compression/CDN, capacity planning, performance budgets, profiling, tail latency, Little's Law. Delivery/operations: CI/CD, infrastructure/configuration as code, immutable infrastructure, GitOps, trunk-based/feature-branch workflows, feature flags, blue-green/canary/rolling deployment, rollback, shift-left test/security, deployment gates, environment parity, reproducible builds, artifact versioning/provenance, secrets rotation, backup/restore, disaster recovery, RTO/RPO, runbooks, release readiness.

### Observability, team, product, and AI engineering

Observability: logs/metrics/traces, OpenTelemetry, SLIs/SLOs/SLAs, error budgets, alerting on user impact, health/readiness/liveness probes, structured logging, cardinality awareness, incident response, root-cause analysis, blameless postmortems, saturation monitoring, synthetic monitoring, production smoke tests. Team practices: Scrum/Kanban, iterative/incremental delivery, Definition of Ready/Done, feedback loops, small PRs, peer review, pair/mob programming, collective ownership, ADRs/RFCs/design reviews, technical spikes, risk-based prioritization, continuous improvement, documentation as delivery. Product qualities: user-centered design, accessibility, progressive disclosure, consistency, visible system status, error prevention/recovery, backward compatibility, extensibility, portability, interoperability, maintainability, reliability, availability, scalability, testability, observability, recoverability, operability, privacy, resource efficiency.

AI/ML/agent engineering: data/model versioning, reproducible experiments, training-serving consistency, model evaluation, independent holdouts, data-leakage prevention, prompt/model versioning, RAG, embedding/vector search, grounding/citation verification, hallucination evaluation, prompt-injection defense, tool permission boundaries, human review, drift monitoring, offline/online evaluation, A/B testing, feedback loops, output validation, fallback/degradation, cost/latency budgets, dataset governance, adversarial testing, privacy-preserving handling, provenance/evidence tracking, deterministic checks around probabilistic outputs, agent state/memory, bounded autonomy/approval gates, isolated tool execution, auditability/reproducibility.

## Task-time evidence record

For each assigned task, the canonical task context should capture:

- task category and risk, acceptance criteria, non-goals, and affected boundaries;
- applicable principle families and the specific design decisions they informed;
- reuse candidates inspected and why they were reused, extended, or rejected;
- data, security/privacy, compatibility, failure, performance, and rollout decisions when relevant;
- checks run and exact evidence/head, review outcome, remaining uncertainty, and rollback/recovery path when needed;
- explicit `not_applicable: reason` for material dimensions that do not apply.

The existing `EngineeringDesignGuard` remains the deterministic contract check for implementation work. Guard and Arena retain their authority; this document does not create permissions, approve risky actions, or replace tests, independent evaluation, human approval, or post-merge verification.


## Runtime applicability and enforcement details

The principle is selected based on the actual deployment and storage model. Do not claim a guarantee that the implementation or provider cannot provide.

### Distributed systems: CAP and PACELC

- **CAP:** during a network partition, a distributed system must choose which operations can succeed consistently and which can remain available. Partition tolerance is a condition the system must handle, not a third property that can simply be switched off.
- **PACELC:** during a partition, state the availability/consistency choice; otherwise state the latency/consistency choice.
- For every shared-state subsystem, record its consistency model (strong, eventual, causal, read-your-writes/session, or local/single-writer), authority/source of truth, and convergence or reconciliation rule.
- Use quorum and a proven consensus protocol only when replicas must agree on values or leadership. If a component is single-process or single-writer, state that boundary instead of adding unnecessary distributed machinery.
- Prevent split brain with explicit ownership and fencing/generation tokens where multiple workers/controllers can compete. Wall-clock timestamps alone do not prove causal order.
- Assume at-least-once delivery can duplicate work; use stable idempotency keys, deduplication, and persisted effect receipts. At-most-once delivery may lose work. Transport alone does not guarantee exactly-once effects.
- Use a transactional outbox/inbox only where a durable state change and event publication must be coordinated. Use sagas and compensating actions only for multi-step workflows that cannot use one atomic transaction.
- Bound queues and dependency pools; apply bulkheads, circuit breakers, deadline propagation, and backpressure where they protect independent work. Reconcile intended state against observed state after uncertain failures.

### Reliability, resilience, and fault tolerance

- Design for process, dependency, network, worker, provider, and agent failure; isolate the blast radius and preserve safe read-only or degraded operation where possible.
- Distinguish liveness (the process is running) from readiness (it can safely accept work).
- Retries must be bounded, use backoff/jitter where appropriate, respect one task-level budget, and avoid replaying irreversible effects without idempotency protection. Exhaustion triggers diagnosis, replanning, or escalation.
- Persist task ownership, progress, and effect receipts before treating them as recoverable. Resume only from validated checkpoints; use fencing or generation checks to reject stale workers.
- Define recovery-time and recovery-point objectives for important workflows where the deployment can measure them. Propose SLOs/error budgets for critical paths instead of inventing unsupported reliability claims.
- Inject controlled timeouts, crashes, stale checkpoints, provider failures, duplicate messages, and corrupted state in tests where those failure modes exist.
- Never report completion when work is incomplete, degraded, unverified, or missing required evidence.

### Correctness, data integrity, and transactions

- State invariants and preconditions/postconditions for critical state transitions. Keep referential links valid and reject conflicting updates using version checks, compare-and-swap, locking, or the store's equivalent.
- Require ACID or serializable behavior only where the underlying store/transaction boundary actually provides it. Do not imply cross-file, cross-database, or remote-service atomicity without a real protocol.
- Version schemas and use expand/migrate/contract or another compatible migration plan; verify data before and after, and define rollback or forward-recovery.
- Choose event sourcing and CQRS only when audit/replay or read/write workload separation justifies their complexity.
- Preserve provenance across intent, task/owner, tool effect, artifact, test/evaluator result, and exact source revision. Evidence must not be reused for a different head or silently overwritten.

### Architecture, verification, and autonomous execution

- Separate policy from mechanism; depend on stable interfaces and ports/adapters. Keep high cohesion and low coupling, version interfaces, and avoid splitting a modular system into services without deployment, scaling, isolation, or ownership benefit.
- Add architecture fitness tests for canonical graph/planner/state/memory/retry/evidence ownership and permitted dependency directions.
- Use fast unit tests, focused integration/contract tests, and a smaller number of end-to-end tests. Add property, fuzz, mutation, differential, fault-injection, supply-chain/provenance, independent holdout, and rollback tests when the risk justifies them.
- Bind build artifacts to controlled inputs, source revision, dependency set, and build evidence. Independent holdouts must not feed tuning or development feedback.
- Every agent action is checked against explicit authority and least-privilege tool scope before side effects. High-impact or irreversible actions use configured human-approval gates.
- Bound each task by deadline, resource/token/cost budget, stop condition, retry budget, and escalation rule. Record uncertainty separately from observations; only verified outcomes can become durable learning.
- Isolate untrusted/new capabilities. Promotion requires independent evaluation and exact-head evidence; preserve the last trusted version and a measurable rollback trigger.
- Record plans, ownership, tool calls, resource usage, evidence, and terminal state. Cross-domain generalization claims require evaluation outside the development domain.

## Pre-implementation blind-spot review

Before non-trivial implementation, challenge the request for contradictions, unsupported assumptions, missing observable acceptance criteria, and hidden compatibility/security/operational constraints. Record findings in the existing orchestration evidence; unresolved explicit conflicts stop dependent graph execution until a decision is documented. Use a constructive format: explain the concern, ask the smallest useful question, recommend a path with trade-offs, and offer a reversible alternative. Continue independent, safe discovery where possible. Deterministic patterns are not complete semantic reasoning; a clear heuristic result does not certify that the requirements are consistent. See [the implementation policy](.ai-harness/ENGINEERING_DESIGN_POLICY.md), [review tests](tests/test_preimplementation_review.py), and [current architecture](docs/ARCHITECTURE_CURRENT.md).

## Required task lifecycle

1. **Classify:** task type, risk, affected boundaries, acceptance criteria, non-goals, and stopping condition.
2. **Select:** applicable principle families and cross-cutting security/correctness/testing/observability/recovery requirements.
3. **Model:** authority, invariants, state ownership, side effects, failure modes, resource budgets, and recovery/rollback.
4. **Implement proportionately:** reuse existing owners and abstractions; record not_applicable: reason for material dimensions that genuinely do not apply.
5. **Verify:** run relevant tests, static/contract checks, benchmarks, threat checks, fault simulations, or independent review. A declaration is not evidence of behavior.
6. **Bind evidence:** link outcomes to the exact final revision and preserve provenance.
7. **Recheck and recover:** after any fix, re-evaluate the exact final head; stale or unavailable checks are not passes. Document rollback or forward recovery for risky changes.
8. **Learn safely:** retain only verified outcomes and never silently broaden authority, permissions, or safety policy.


## AI-era foundation principles (mandatory)

The 20-principle catalogue is maintained in `portable/ai_era_principles.py` and explained in `docs/AI_ERA_FOUNDATION_PRINCIPLES.md`. The runtime records a digest-bound catalogue receipt before StateGraph node execution. This receipt is traceability evidence, not a substitute for the existing deterministic design guard, authorization, independent verification, recovery, or promotion gates.

| ID | Foundation principle | Enforced intent |
|---|---|---|
| IPACT | Intent Before Action | Bind work to intent, constraints, acceptance criteria, and stopping conditions. |
| VERA | Verification Before Acceptance | Require relevant, revision-bound verification evidence. |
| PACT | Permission And Control of Tools | Keep actions within granted authority and scope. |
| INVAR | Invariant Preservation | Preserve essential correctness, security, compatibility, and resource invariants. |
| TRACE | Traceable Results And Claims Engineering | Trace important claims to sources, revisions, and evidence. |
| R3 | Recover, Resume, Roll Back | Provide safe interruption, resumption, and rollback paths. |
| RAIL | Resource-bounded Autonomous Iteration Limits | Bound time, cost, retries, and resources; stop on stalled progress. |
| CIG | Context Integrity Guard | Use relevant, current, authoritative context. |
| BLAST | Bounded Limits And Scoped Trust | Contain failures and limit blast radius. |
| INDEP | Independent Evaluation Protocol | Challenge results beyond the producer's own claims. |
| OWN | Ownership With Named Accountability | Assign responsibility for tasks, state, and handoffs. |
| LEARN | Learning Evaluated Against Regression and Novelty | Learn from validated outcomes without weakening guarantees. |
| DELTA | Defensible Engineering with Limited, Targeted Alterations | Make the smallest justified change without sacrificing safety or completeness. |
| HANDOFF | Handoff And Negotiated Definition Of Function | Define delegation scope, inputs, outputs, acceptance, and failure behavior. |
| DEGRADE | Designed, Efficient, Adaptive Graceful Recovery Under Degraded Environments | Preserve safe core behavior or pause safely when dependencies fail. |
| TRUST | Treat Retrieved Untrusted Sources Thoughtfully | Separate untrusted data from instructions and permissions. |
| PROMOTE | Proof-Required Operational Maturation Of Tested Enhancements | Promote capabilities only after required evaluation and rollback checks. |
| ECONOMY | Engineering Cost Optimization Measured Over the Full Lifecycle, Not Yield Alone | Optimize total lifecycle cost and risk, not generation speed alone. |
| MEASURE | Metrics, Evidence, And Results Evaluated | Measure outcomes, quality, recovery, and cost rather than activity alone. |
| CONTEXT | Context Is an Engineering Dependency | Retrieve relevant, current, authoritative context before acting. |

Applicability remains risk-based. The canonical EngineeringDesignGuard and existing security, verification, recovery, memory/context, and promotion gates remain the enforcement authorities. Do not claim a principle is fully enforced merely because it is catalogued or a receipt exists. External evaluator independence and operator-run deployment/rollback rehearsals still require their own evidence.
