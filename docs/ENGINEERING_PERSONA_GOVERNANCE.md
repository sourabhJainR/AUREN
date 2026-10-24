# AUREN engineering persona and observability contract

## Purpose

Use separate agents for planning, implementation, and independent review. Record their work in the same AUREN trace and evidence lineage. This contract does not add a second scheduler, policy engine, or source of truth.

## Required role separation

1. **Planner** creates an EngineeringPlan with goal, scope, assumptions, acceptance criteria, dependencies, alternatives, risks and mitigations, test strategy, budget, source revision, and rollback plan.
2. **Plan reviewer** challenges completeness, feasibility, dependency order, unnecessary complexity, security assumptions, measurable acceptance criteria, and rollback coverage. It must approve before implementation starts.
3. **Implementer(s)** work from the digest-bound approved plan. They provide artifact references and verification evidence; their own completion statement is not proof.
4. **Implementation reviewer** is a separate persona and agent identity. It inspects the actual artifacts/diff, test evidence, security boundaries, compatibility, failure handling, and regression risk. It cannot approve its own output.

The runtime rejects reused agent IDs and identical planning/implementation personas. Plan and implementation reviewers must also be distinct from the planner and implementer and from each other. A review approval requires evidence references. A review with unresolved findings cannot be approved.

## Execution contract

- Planning and plan review happen before the implementation callback is invoked.
- The plan digest is computed from canonical plan content. A supplied digest that does not match the content is rejected.
- The approved plan is attached to the task context given to the implementer.
- The implementation review runs after the worker returns its artifact summary and verification references.
- High-risk/critical tasks and release transitions require the independent persona workflow. Legacy low-risk execution can remain available for compatibility, but it is explicitly marked as not independently reviewed and cannot authorize promotion.
- An unapproved or malformed review blocks execution or promotion. Do not automatically replay a worker after rejection if its side effects are not proven idempotent; record the findings and create a bounded corrective task after reconciliation.
- Review findings, reviewer identities, evidence references, plan digest, and lifecycle spans are correlated to the same trace.
- Observability export failures are recorded as telemetry failures and must not alter the canonical task result or trigger side-effect replay.

## Observability and evaluation

Every span has explicit parentage; append order does not imply a parent-child relationship. Event payloads are bounded and sanitized. Unsupported objects are represented by a type marker instead of invoking arbitrary repr code. Prompt versions are immutable by name/version. Empty datasets, missing scores, inconsistent score dimensions, non-finite scores, and incomplete evaluation coverage are unscored errors—not passing results.

The local exporter is optional and local-first. It does not grant execution authority. A production deployment should export only through an explicitly configured, access-controlled adapter and must not include prompts, credentials, or raw private payloads by default.

## Gate sequence

A. Validate schema, privacy, concurrency, prompt/dataset immutability, and fail-closed evaluation behavior.
B. Wire phase-aware traces through ideation, research, planning, context assembly, provider routing, tools, artifact changes, verification, learning, release, and rollback.
C. Use reviewed traces to create versioned regression cases; compare candidates on the same pinned dataset and transfer suite.
D. Calibrate per-domain evaluators and drift alerts before promoting metrics into blocking guardrails.
E. Project durable run/evidence state into the Engineering Console and test restart, cancellation, partial traces, export failure, and rollback.

## Non-negotiable limits

- Traces explain behavior; evaluators score behavior; the canonical runtime/policy layer authorizes side effects and promotion.
- Unknown, missing, stale, corrupt, or unscored evidence never becomes a pass or numeric zero.
- Repository CI does not prove production isolation, independent evaluator custody, sealed holdout custody, or operator rollback readiness.
- Changes require regression evaluation, exact-head CI, independent review, and a documented rollback path.

## Persistent evaluation evidence

Reviewed regression datasets, calibration reports, explicit guardrail approvals, and drift alerts use the local SQLite observability store under the canonical AUREN runtime home. Dataset name/version pairs are immutable. Calibration is recorded separately from approval; a calibrated threshold cannot become a blocking guardrail without a distinct reviewer identity and evidence reference. Drift alerts are append-only and are exposed to the console through a read-only projection. The console does not create the store or fabricate missing alert data.
