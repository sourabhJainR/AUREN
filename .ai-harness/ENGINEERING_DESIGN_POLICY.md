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

## Enforcement

`portable/engineering_design_guard.py` provides the deterministic design-contract and curated-change gate. It infers relevant dimensions from intent and changed paths, validates declared design evidence, emits findings, and creates a content-addressed receipt suitable for the existing evidence/provenance chain.

The guard remains advisory for low-risk work and blocks higher-risk work when configured safety-critical evidence is missing. It never executes code, grants permissions, or performs external side effects. Runtime behavior remains unchanged; the gate changes planning/verification requirements rather than application execution.
