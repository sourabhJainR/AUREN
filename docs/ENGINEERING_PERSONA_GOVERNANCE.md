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

## Gate A and Gate B implementation evidence

### Gate A — observability correctness and privacy

The PR implements recursive telemetry sanitization (including quoted multi-token secret assignments and bearer values), bounded payloads, recursively immutable registered prompt metadata, immutable dataset revisions, fail-closed empty/incomplete/non-finite evaluation handling, explicit parent validation, serialized append writes, event digests and sequence-gap detection. Corrupt or interrupted histories are reported as partial rather than complete. Exporter failures remain telemetry failures and do not authorize replay or change the canonical task result.

Deterministic regressions cover redaction bypasses, prompt metadata mutation, empty/inconsistent evaluation datasets, invalid span parents, concurrent writes, unsupported objects, and partial/tampered event histories. Rollback is an isolated revert of this PR, followed by verification that the previous runtime revision and state are restored; release changes must use the existing artifact rollback path, not telemetry state.

### Gate B — canonical lifecycle instrumentation

StateGraph.run with an injected tracer now instruments the actual StateGraph supervisor invocation and its canonical node results. Supervisor and node spans carry stable task/run/session correlation, graph and intent digests, revision metadata, explicit parentage, node status, attempts, repair counts, and evidence counts. The trace's terminal status is derived from the returned OrchestrationRun, not from worker self-report. The coding agency additionally emits canonical evidence, verification, evaluation, regression-feedback, and release/rollback spans. Regression feedback is derived from the runtime regression result and records explicitly that it did not itself mutate a model or policy.

Phase labels on supervisor nodes are derived from the node name and kind. They are useful for coverage and diagnostics, but they are not a substitute for instrumenting a real provider/tool adapter. Worker-supplied provider, research, tool, artifact, and learning observations remain diagnostic only.

### Acceptance still required before claiming full production closure

- Wire actual research/retrieval, provider routing, tool invocation, artifact storage, learning-candidate persistence/promotion, and release/rollback adapters to the same canonical trace context; do not promote worker-reported events into authority.
- Exercise process interruption, restart/resume, cancellation, partial writes, and duplicate dispatch against the deployed supervisor and verify that correlated traces match durable canonical state.
- Prove independent evaluator custody, sealed holdout custody, production isolation, and a rehearsed rollback with evidence.

CI validates repository behavior only. Until the external acceptance items above have evidence, Gate B is implemented at the supervisor/runtime observation boundary but is not certified as fully closed end-to-end in production.

## Non-negotiable limits

- Traces explain behavior; evaluators score behavior; the canonical runtime/policy layer authorizes side effects and promotion.
- Unknown, missing, stale, corrupt, or unscored evidence never becomes a pass or numeric zero.
- Repository CI does not prove production isolation, independent evaluator custody, sealed holdout custody, or operator rollback readiness.
- Changes require regression evaluation, exact-head CI, independent review, and a documented rollback path.

## Persistent evaluation evidence

Reviewed regression datasets, calibration reports, explicit guardrail approvals, and drift alerts use the local SQLite observability store under the canonical AUREN runtime home. Dataset name/version pairs are immutable. Calibration is recorded separately from approval; a calibrated threshold cannot become a blocking guardrail without a distinct reviewer identity and evidence reference. Drift alerts are append-only and are exposed to the console through a read-only projection. The console does not create the store or fabricate missing alert data.
