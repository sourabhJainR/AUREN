# Change Quality and Reuse Contract

## Goal

Make every AUREN-assisted repository change deliberately curated: reuse existing implementations where practical, preserve usage patterns, implement the complete requested behavior, minimize data-access cost, protect performance, follow local observability/error conventions, and verify against regressions before completion.

## Approved implementation

The implementation extends the existing `portable.engineering_design_guard.EngineeringDesignGuard` and the existing AI Coding Orchestrator skill surfaces. It adds no parallel workflow engine, planner, evidence store, repository index, logging abstraction, benchmark framework, or runtime service.

For code changes, the system records and verifies the relevant chain:

`reuse candidates -> usage compatibility -> data/DB access -> performance -> logging/telemetry -> exception handling -> regression safety net`

Existing public APIs, callers, configuration semantics, persisted contracts, lifecycle ordering, command flows, and user-facing workflows remain compatibility surfaces unless the requested work explicitly changes them.

When data access changes, the implementation prefers already-loaded state, established repositories/caches, batching/set-based operations, and shared transaction/connection handling while avoiding N+1 access and duplicate round trips. Performance-sensitive changes preserve established resource behavior unless intentionally changed and are measured when static reasoning is insufficient.

Logging, telemetry, exception propagation, cleanup, and translation follow the repository's established conventions. Regression verification covers requested functionality and affected existing workflows using repository-native tests and checks. A concern may be recorded as `not_applicable: reason` when genuinely irrelevant; silent omission is not treated as evidence.

## Enforcement

`EngineeringDesignGuard.review(...)` remains backward-compatible and deterministic. It emits warnings for missing quality evidence on ordinary work and blocks configured safety-critical omissions on high-risk code changes. The guard is a design/evidence gate; runtime application behavior is unchanged.
