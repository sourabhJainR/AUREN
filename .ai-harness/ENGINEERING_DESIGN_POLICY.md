# Engineering Design Policy

This policy distills the engineering intent of the 14 rule sets in `mattpocock/agent-rules-books` into one repository-native operating model. It is guidance for the AUREN system, not copied book text and not a substitute for the books.

## Source coverage

| Source | Constructive contribution to AUREN |
| --- | --- |
| A Philosophy of Software Design | Minimize cognitive load; prefer deep modules, information hiding, precise interfaces, localized complexity, and meaningful names. |
| Clean Architecture | Keep policy independent from frameworks and details; enforce inward dependencies and humble adapters. |
| Clean Code | Optimize for local readability, focused functions, explicit mutation, clear APIs, and behavior-focused tests. |
| Code Complete | Make construction deliberate: clear requirements, types, control flow, validation, diagnostics, incremental verification, and measured tuning. |
| Designing Data-Intensive Applications | Make ownership, consistency, durability, retries, replay, ordering, evolution, partitioning, replication, and repair semantics explicit. |
| Domain-Driven Design | Model domain language and invariants inside explicit bounded contexts; keep integration and persistence from defining the model. |
| Domain-Driven Design Distilled | Apply DDD selectively; invest in core domain complexity and avoid ceremony for simple CRUD or technical problems. |
| Implementing Domain-Driven Design | Protect aggregate boundaries, identity, value objects, aggregates, domain events, application services, and translation boundaries. |
| Patterns of Enterprise Application Architecture | Choose patterns from actual forces; separate presentation, workflow, domain, persistence, transactions, concurrency, and integration. |
| Refactoring | Make behavior-preserving structural changes in small, reversible, verifiable steps. |
| Refactoring.Guru | Diagnose the smell first, choose the smallest treatment, preserve compatibility, and stop when the blocking smell is resolved. |
| Release It! | Design for failure, timeouts, bounded retries, isolation, overload, observability, safe deployment, and recovery. |
| The Pragmatic Programmer | Keep one source of truth, automate repeatable work, shorten feedback loops, own outcomes, and keep volatile choices reversible. |
| Working Effectively with Legacy Code | Gain control before redesign: characterize behavior, create small seams, break blocking dependencies, then change and refactor locally. |

## Expanded deterministic contract dimensions

The canonical `portable.engineering_design_guard.EngineeringDesignGuard` is the single deterministic declaration gate. The catalogue is broad; the guard selects dimensions from task intent, changed paths, and risk. Declarations are a contract for subsequent tests/review, not proof that the implementation is safe.

| Dimension | Evidence expected when applicable | High-risk behavior |
|---|---|---|
| Security and privacy | Trust boundary/threat model; authorization and least-privilege/tool authority; input handling, secrets and sensitive-data decisions | Block acceptance when material trust/authority contracts are missing |
| Performance and resources | Measurable budget or baseline, workload/scale assumptions, benchmark/profile evidence where warranted, bounded resource use | Block missing evidence for high-risk hot paths/resource limits; never invent benchmark results |
| Concurrency | State ownership, synchronization or immutability/message passing, cancellation/lifecycle, race/deadlock verification | Block when ownership or safety/liveness contract is absent |
| Distributed systems | Consistency choice and CAP/PACELC trade-off where partitions/replicas apply; duplicate delivery/idempotency, ordering, quorum/split-brain and recovery as relevant | Block missing consistency/failure semantics for high-risk distributed work |
| Data correctness | Invariants, source of truth, transaction/isolation behavior, migration compatibility, reconciliation/recovery | Block missing correctness contracts for high-risk data changes |
| Operations and recovery | Observability, rollout/rollback, recovery and SLO/RTO/RPO where relevant | Block high-risk changes without a recovery path |
| AI/agent authority | Tool permissions, bounded autonomy, approval boundary, evaluation and evidence provenance | Block high-risk autonomous/tool-execution work without authority and evaluation contracts |

Use `not_applicable: reason` only when the dimension truly does not apply. Do not label a dimension N/A merely because evidence is inconvenient to obtain. CAP is a partition-time trade-off; PACELC additionally makes normal-operation latency/consistency trade-offs explicit. Never claim a system can guarantee all CAP properties during a partition.

## One integrated model

AUREN should not load all 14 sources as equal instructions on every task. They are capability lenses selected by task shape. The default integrated model is:

```text
INTENT
  -> MODEL
  -> BOUNDARIES
  -> OWNERSHIP
  -> REUSE
  -> CHANGE
  -> DATA/PERFORMANCE
  -> FAILURE/OBSERVABILITY
  -> VERIFY/REGRESSION
  -> REVIEW
  -> RELEASE
  -> LEARN
```

### 1. Complexity and module design

For every non-trivial design or refactor, prefer lower cognitive load over lower line count. A new abstraction must hide meaningful complexity. Avoid pass-through layers, generic utility dumping grounds, speculative interfaces, exposed representation, temporal coupling, and boolean mode flags that force callers to understand internals.

### 2. Reuse-first change selection

Before creating a new class, function, adapter, client, query path, repository method, helper, utility, workflow step, configuration mechanism, or abstraction, inspect the repository for maintained implementations serving the same responsibility. Prefer direct reuse, narrow extension, composition, or adaptation over duplication. Record the relevant candidate(s) inspected and why a new implementation was necessary when reuse was rejected.

Reuse must not change externally observable usage without an explicit requirement. Preserve public API shape, caller contracts, configuration behavior, lifecycle ordering, persisted data contracts, command flows, and user-facing workflow. A smaller diff is not a valid reason to bypass required functionality or safety behavior.

## Proactive repository-native approach recommendations

For every non-trivial task, call `EngineeringDesignGuard.recommend_approaches(root, intent, changed_paths)` before committing to a design. The deterministic advisor scans bounded repository manifests, CI and canonical-owner paths; it reports only evidenced languages/frameworks/versions, candidate algorithms or patterns, trade-offs, alternatives and source paths. It must prefer existing owners and repository test conventions, avoid dependencies/framework assumptions without manifest evidence, and set `needs_stack_confirmation` when the stack cannot be established. Recommendations are advisory: acceptance, user authority, tests, security gates and promotion remain separate. Add rules only when tied to a real repository pattern and regression tests; do not build a generic pattern catalogue disconnected from the working stack.

### 3. Architecture and dependency direction

Keep business policy independent from frameworks, transports, persistence, vendors, and deployment details. Inner policy owns the interfaces it needs; outer adapters implement them. Organize by capability/use case/domain where that makes ownership clearer. Enforce important boundaries mechanically when practical.

### 4. Domain ownership

When domain complexity is material, define the bounded context and local vocabulary before shaping code. Keep invariants with the concept that owns them. Use entities, value objects, aggregates, domain services, repositories, and domain events only when they clarify real domain forces. Reference other aggregates by identity and translate foreign models at boundaries.

### 5. Enterprise pattern selection

Patterns are tools, not architecture by decoration. Select Transaction Script, Table Module, Domain Model, Service Layer, Repository, Data Mapper, Gateway, Unit of Work, Identity Map, DTO, facade, locking, and related patterns from concrete forces. Reject generic repositories, ORM-as-domain-model leakage, pass-through services, hidden transaction ownership, and remote object-shaped APIs.

### 6. Data, database access, and distributed-system semantics

Every important data flow must identify source of truth, derived data, consistency, durability, visibility, idempotency, ordering, schema evolution, failure recovery, and repair. For database or remote reads, prefer already-loaded state, existing caches, batching, set-based operations, and established repository/query paths. Avoid N+1 calls, duplicate round trips, per-record queries, redundant hydration, and repeated remote fetches. Preserve transaction and consistency semantics rather than optimizing a query in isolation.

When access cost could materially change, the design evidence should state the expected query/request count or complexity and how the existing behavior is preserved or intentionally changed.

### 7. Performance parity

Performance is a compatibility concern for existing hot paths unless the requirement explicitly changes it. Identify affected latency, throughput, memory, allocation, CPU, I/O, concurrency, and queue behavior when material. Reuse existing batching, caching, pooling, parsing, serialization, and scheduling mechanisms before adding new ones. Avoid unnecessary allocations, repeated parsing/serialization, blocking work, unbounded queues, and retry amplification. Measure when static reasoning cannot establish parity safely.

### 8. Construction, logging, and exception handling

Build in small verifiable increments. Use explicit names, narrow scopes, clear control flow, meaningful types, deliberate error handling, input validation at trust boundaries, and tests that cover normal, boundary, invalid, and failure behavior.

Logging and telemetry must follow the repository-native framework, levels, structured fields, correlation/context propagation, redaction, and sampling conventions. Do not introduce a second logging abstraction. Exceptions should follow existing types and translation boundaries, preserve diagnostic context, clean up owned resources, and never silently swallow failures.

### 9. Refactoring and legacy change

Classify work as feature, bug fix, or refactor. For refactors, preserve observable behavior and use small transformations. For poorly understood code, characterize behavior first and create the smallest useful seam. Do not combine broad cleanup, redesign, and behavior change in one opaque patch. Temporary seams need a cleanup path.

### 10. Production resilience

Production readiness includes timeouts, bounded retries, backoff/jitter, bulkheads, circuit breaking where appropriate, finite queues, load shedding, validation of external responses, structured observability, safe startup/migrations, authorization, auditability, and rollback or roll-forward paths. Resource ownership and failure cleanup are explicit.

### 11. Regression safety

A feature or fix is not complete when only the new path works. Verify the requested behavior and adjacent existing behavior through the repository's established unit, integration, contract, build, static, security, data, and performance checks where relevant. Add focused regression tests around the changed contract and failure/boundary paths. A new test cannot replace verification of an existing workflow that could be affected.

### 12. Pragmatic feedback and learning

Prefer reversible decisions when evidence is weak. Use tracer slices or time-boxed prototypes to test expensive assumptions. Automate repeated checks. Keep one authoritative source for each system fact. A learning candidate may improve routing or context selection only after regression and safety evidence; it may never weaken security, permission, approval, or immutable safety controls.

## Conflict handling

When guidance pulls in different directions, use this order:

1. Repository/team instructions and acceptance criteria.
2. Security, privacy, permissions, and human approval boundaries.
3. Correctness and explicit contracts.
4. Existing maintained architecture, implementations, and ownership.
5. Operational, data-access, performance, and compatibility requirements.
6. Complexity reduction and maintainability.
7. Minimal implementation cost.

Known compatibility constraints from the source collection must also be respected. In particular, DDD and IDDD should not be combined with enterprise patterns that directly contradict their domain ownership model; where a pattern conflicts with the chosen domain architecture, select the domain model and adapt the enterprise mechanism at the boundary.

## Required design evidence

For substantial or risk-sensitive changes, the run should capture only the evidence relevant to the task:

- responsibility/ownership and affected boundary;
- existing implementations inspected, reuse/adaptation decision, and reason for any new abstraction;
- source of truth and data consistency semantics when data changes;
- DB/query/remote access pattern, batching/reuse strategy, and N+1/duplicate-round-trip avoidance when data access changes;
- performance impact or explicit `not_applicable: reason` when no material performance concern exists;
- failure model, timeout/retry/recovery behavior when external or asynchronous work changes;
- repository-native logging/telemetry conventions and exception propagation/cleanup behavior;
- behavior delta, compatibility of usage patterns, and regression safety net when changing existing code;
- domain language/context and invariant ownership when domain complexity changes;
- compatibility and rollout path for public or persisted contracts;
- verification performed and remaining uncertainty.

A missing dimension is acceptable when the guard records `not_applicable` with a reason. Silent omission is not treated as evidence.

## Task-time applicability contract

Every assigned task uses this policy through the existing task planner, engineering-design guard, verification/review lifecycle, and evidence chain. The planner must select applicable lenses before work begins and retain concise decisions/evidence in the canonical task context; it must not create a second policy engine or parallel evidence store. For each material lens, state the design decision and its evidence, or state `not_applicable: reason`. An unmentioned dimension is not the same as an explicit non-applicability decision.

| Task shape / trigger | Required lenses to consider | Minimum evidence when applicable |
|---|---|---|
| Any task | Correctness, acceptance criteria, KISS, YAGNI, least surprise, evidence over assumption, source of truth | Goal, boundaries, acceptance checks, non-goals, stop condition, and verified outcome |
| Code, feature, or bug fix | SOLID, DRY, cohesion/coupling, encapsulation, composition, reuse-first, fail-fast | Existing implementation search, ownership/boundaries, focused change, failure/boundary tests, regression evidence |
| Architecture or refactor | GRASP, SoC, dependency direction, information hiding, locality of change, reversible decisions | Responsibility assignment, dependency impact, behavior delta, compatibility and rollback/roll-forward plan |
| Domain-heavy business logic | DDD, invariants, value objects/entities/aggregates, bounded contexts, domain events when warranted | Domain language, invariant owner, consistency/transaction boundary; justify when simple CRUD needs no DDD ceremony |
| Data, persistence, or integration | Single source of truth, ACID/consistency, idempotency, ordering, schema evolution, DRY, avoid N+1 | Read/write ownership, query/request counts where material, durability/replay/repair, migration and data recovery plan |
| Distributed or async work | Timeouts, bounded retries, backoff, cancellation, bulkheads, circuit breakers, backpressure, graceful degradation | Failure modes, resource bounds, duplicate handling, observability, recovery behavior |
| Security or privacy | Least privilege, defense in depth, secure defaults, input validation, secret/data minimization, threat modeling | Trust boundaries, authorization checks, sensitive-data handling, negative tests, auditability |
| Tests or quality tooling | TDD/BDD when useful, test pyramid, deterministic tests, contract/regression/property/fuzz/mutation tests when appropriate | Test selection rationale, affected behavior, reproducible result; coverage alone is not proof |
| Performance-sensitive work | Measure before optimize, algorithmic complexity, batching/caching/pooling where warranted, tail latency | Baseline or reasoned expectation, resource/query impact, benchmark/profile when static reasoning is insufficient |
| Release, CI/CD, or operations | Reproducible builds, least privilege, deployment gates, observability, rollback, backup/restore, SLO/RTO/RPO as relevant | Exact-head checks, artifact/provenance, rollout gates, health signals, recovery and rollback verification |
| AI/agent/model work | Independent holdouts, provenance, prompt/tool boundaries, bounded autonomy, deterministic checks around probabilistic outputs | Evaluation dataset separation, evidence lineage, tool permissions/isolation, uncertainty/failure handling, regression and rollback gates |
| Documentation or research | Accuracy, source provenance, least astonishment, actionable output, no unsupported claims | Sources or repository evidence, scope and uncertainty, next action; no code-only checklist forced where irrelevant |

Principle selection is risk-based: low-risk tasks keep the contract lightweight; cross-cutting, security-sensitive, data-changing, production, irreversible, or repeated-failure tasks receive deeper review and fail closed when required evidence is absent. A principle may be marked not applicable only with a specific reason. Recommendations and model confidence never substitute for test results, source evidence, or user approval.

## Enforcement

`portable/engineering_design_guard.py` provides the deterministic design-contract and curated-change gate. It infers relevant dimensions from intent and changed paths, validates declared design evidence, emits findings, and creates a content-addressed receipt suitable for the existing evidence/provenance chain.

The guard remains advisory for lower-risk work and blocks higher-risk work when safety-critical evidence is missing. It never executes target code or grants permissions. The orchestrator invokes it before graph nodes can perform side effects, records its receipt in the existing run evidence, and blocks code-task acceptance unless verification names the exact final code revision. The gate does not replace tests, independent evaluation, or approval controls.


## Canonical 12-family task selection

Use the 12-family taxonomy in [ENGINEERING_PRINCIPLES.md](../ENGINEERING_PRINCIPLES.md) as the shared vocabulary: fundamental design; security/privacy; performance/cost; concurrency; distributed systems; reliability/recovery; data/correctness; API/integration; testing/verification; delivery/operations; observability/governance; AI/autonomous systems. The catalogue is not a parallel policy engine. The EngineeringDesignGuard remains the deterministic declaration gate; tests, reviewer judgment, approval gates, and independent evidence remain necessary to prove behavior.

For distributed work, require explicit consistency and CAP/PACELC choices only where replicas, partitions, remote dependencies, or multiple writers make them meaningful. Record a single-writer/local-state boundary when that is the real model. Do not claim consensus, quorum, ACID, exactly-once effects, SLOs, or recovery guarantees unless the runtime/store and evidence support them.

For every applicable task, the lifecycle is: classify task/risk; select principle families; record risks, authority, invariants, ownership, side effects and recovery; implement proportionately; verify with suitable tests or evidence; bind evidence to the exact final revision; and preserve rollback/forward recovery for risky changes. A declared principle or design receipt is a contract to verify, not proof that the implementation satisfies it.


## Repository-native approach recommendation

Before a non-trivial implementation, call `EngineeringDesignGuard.recommend_approaches(repository_root, intent, changed_paths, risk=...)` (or `AdaptiveRuntime.recommend_design(...)`). Review its repository-manifest/CI evidence, existing-owner candidates, suggested algorithm and workflow, alternatives, complexity/resource trade-offs, required verification, and rollback gates. Use recommendations to choose among approaches that fit the current language, framework, versions, and conventions; do not add a new stack, dependency, store, scheduler, or abstraction merely because a pattern is available. Unknown or conflicting stack evidence must remain unresolved and be verified before version-specific APIs are selected. The advisor is deterministic and advisory: it never substitutes for tests, review, authorization, or promotion gates.
