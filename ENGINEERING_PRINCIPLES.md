# AUREN Root Engineering Principles Contract

This is the compact root contract for all assigned work in AUREN. It is applied through `AGENTS.md`, the AI Coding Orchestrator, `.ai-harness/ENGINEERING_DESIGN_POLICY.md`, `portable/engineering_design_guard.py`, and the existing task/evidence/verification lifecycle. It is not a second policy engine.

## Non-negotiable operating rules

1. Start from the task intent, acceptance criteria, constraints, non-goals, affected boundaries, and stopping condition.
2. Inspect the repository and search for existing implementations before adding abstractions, dependencies, patterns, or parallel infrastructure.
3. Select principles by task shape and risk. Apply the smallest useful set; do not turn principles into rituals or use patterns without a concrete reason.
4. Record material design choices and evidence in canonical task context. For each important lens, provide evidence or `not_applicable: reason`; never silently omit it.
5. Preserve security, privacy, authorization, compatibility, existing contracts, and user approval boundaries. User and repository requirements outrank style preferences; safety and permission boundaries cannot be waived by a task plan.
6. Verify outcomes with the strongest relevant evidence: tests, build/type/lint/static checks, contract tests, security checks, performance evidence, source citations, independent review, or production/rollout signals.
7. Re-evaluate after fixes on the exact final commit. Missing, stale, queued, skipped, or failed evidence is not a pass. Use bounded retries, change approach when a repeated hypothesis fails, and preserve the last verified state and rollback path.
8. Learn from verified outcomes only. Never silently modify permanent rules, credentials, permissions, approval gates, or safety controls.

## Principle catalogue and when to apply it

| Principle family | Consider these | Apply when |
|---|---|---|
| Fundamental design | SOLID (SRP, OCP, LSP, ISP, DIP), DRY, KISS, YAGNI, SoC, SLAP, Law of Demeter, POLA, fail-fast, explicit failure, information hiding, encapsulation, abstraction, immutability, least privilege | Every task considers simplicity, clarity, ownership, and safety; use specific principles where design choices arise |
| Responsibility and collaboration | GRASP (Information Expert, Creator, Controller, Low Coupling, High Cohesion, Polymorphism, Pure Fabrication, Indirection, Protected Variations) | Object-oriented responsibility allocation, service boundaries, and collaboration design |
| Object and design patterns | Factory, Abstract Factory, Builder, Prototype, Singleton; Adapter, Bridge, Composite, Decorator, Facade, Flyweight, Proxy; Chain of Responsibility, Command, Iterator, Mediator, Observer, State, Strategy, Template Method, Visitor; Repository, Unit of Work, Specification, DTO, Null Object, DI | Only when they address a real construction, structure, behavior, persistence, or dependency problem; avoid pattern-by-decoration |
| Domain and architecture | DDD, entities, value objects, aggregates/invariants, bounded contexts, domain events, Clean/Onion/Hexagonal, layered architecture, modular monolith, microservices, plugin architecture, ports/adapters | Domain complexity, module ownership, dependency direction, deployment boundaries, or extensibility warrant them |
| API and integration | REST/resource design, statelessness, idempotency, versioning, backward compatibility, consumer-driven contracts, schema evolution, API gateway/BFF, webhooks, anti-corruption layer | Public APIs, service integration, or external contracts change |
| Distributed systems | CAP/PACELC trade-offs, consistency, replication, partitioning, quorum, leader election, saga, outbox/inbox, deduplication, dead-letter queue, backpressure, retries/timeouts, circuit breaker, bulkhead, graceful degradation | Network, queue, multi-process, asynchronous, or partial-failure behavior exists |
| Data | Normalization/denormalization, ACID/isolation, keys/indexes, optimistic/pessimistic concurrency, query plans, batching, caching, CDC, migrations, data lineage, retention, multi-tenant isolation | Persisted data, queries, data contracts, tenant boundaries, or data lifecycle changes |
| Security and privacy | Zero Trust, defense in depth, secure defaults/failure, authentication vs authorization, RBAC/ABAC, threat modeling, input validation, output encoding, secret handling, encryption, audit, SBOM/supply chain, data minimization, privacy by design | Always consider trust boundaries; increase evidence for sensitive or externally reachable paths |
| Testing and quality | TDD/BDD/ATDD, unit/integration/contract/E2E, regression, property-based, mutation, fuzz, differential, golden-master, boundary analysis, deterministic tests, test doubles, risk-based testing | Choose tests based on behavior, failure modes, risk, and the existing test architecture; coverage alone is insufficient |
| Code health | Readability, naming, small focused functions, strong types, explicit errors, few side effects, no magic values, static analysis, refactoring, code review, technical-debt management | Every code change |
| Concurrency and performance | Thread safety, atomicity, structured concurrency, cancellation, resource ownership, bounded queues, algorithmic complexity, profiling, caching, batching, pooling, tail latency, load shedding | Parallel/asynchronous work or material latency, throughput, memory, CPU, I/O, or capacity concerns |
| Delivery and operations | CI/CD, IaC, GitOps, reproducible builds, feature flags, blue-green/canary/rolling releases, deployment gates, rollback, backup/restore, RTO/RPO, runbooks | Build, release, runtime, migration, infrastructure, or recovery changes |
| Observability | Structured logs, metrics, traces, correlation IDs, OpenTelemetry, SLIs/SLOs/SLAs, error budgets, health/readiness probes, alerts, incident reviews | Runtime behavior, operations, external calls, asynchronous processing, or failure diagnosis changes |
| Team practice | Small PRs, peer review, pair programming, ADR/RFC, definition of done, incremental delivery, risk-based prioritization, documentation | Shared ownership, significant design choices, or delivery coordination |
| AI/agent systems | Independent holdouts, data-leakage prevention, model/prompt versioning, RAG grounding, citation checks, prompt-injection defense, tool permissions, human approval, drift monitoring, provenance, bounded autonomy, deterministic validation | Any AI/ML/agent/model, retrieval, tool-execution, or learned-policy work |
| Product quality | Accessibility, usability, interoperability, portability, reliability, availability, maintainability, operability, recoverability, resource efficiency | User-facing features, product contracts, and system-level quality decisions |

## Task-time evidence record

For each assigned task, the canonical task context should capture:

- task category and risk, acceptance criteria, non-goals, and affected boundaries;
- applicable principle families and the specific design decisions they informed;
- reuse candidates inspected and why they were reused, extended, or rejected;
- data, security/privacy, compatibility, failure, performance, and rollout decisions when relevant;
- checks run and exact evidence/head, review outcome, remaining uncertainty, and rollback/recovery path when needed;
- explicit `not_applicable: reason` for material dimensions that do not apply.

The existing `EngineeringDesignGuard` remains the deterministic contract check for implementation work. Guard and Arena retain their authority; this document does not create permissions, approve risky actions, or replace tests, independent evaluation, human approval, or post-merge verification.
