# AUREN Basic Operating Mode: Functional and Non-Functional Requirements

**Status:** Proposed operating contract; implementation tracked separately  
**Baseline main:** `93f40ae42d691801de9b000b21fec42b888ede6c`  
**Stable work key:** `AUREN:issue-696:continuous-engineering`  
**Scope:** #696, with dependencies on #998, #692, #694, #534, #1256, #1055, #909, #1193, #404 and #1038

## 1. Purpose

Every AUREN engineering work unit must establish three things before it spends meaningful time or causes a side effect:

1. **Technology readiness:** Does AUREN have the required code, tools, provider capabilities, permissions, environment and verification path?
2. **Intent readiness:** Is the requested outcome clear, bounded, authorized and testable?
3. **Cognitive readiness:** Does the active execution have enough fresh context, domain knowledge, dependency understanding and independent verification capacity to make a safe decision?

AUREN must record every detected gap, assign an existing owner, and provide a bounded path to close it. A gap is not hidden in model prose or silently treated as solved.

This is an operating contract for the existing AUREN runtime. It does not create another orchestrator, state store, agent hierarchy, or source of truth.

## 2. Basic mode of operation

Basic mode is the default path for every new or resumed work unit. It is deliberately small: assess first, research only when needed, prove uncertain assumptions with a small POC, then execute and verify.

### 2.1 Lifecycle

1. **Identify:** Create or resume the stable work key and canonical work-unit ID. Reject duplicate active work for the same key unless the supervisor explicitly authorizes a distinct child unit.
2. **Capture intent:** Normalize the request into goal, user-visible outcome, acceptance criteria, scope, exclusions, constraints, authority, risk tier and deadline.
3. **Scan technology:** Inspect current repository/main SHA, relevant code and tests, tool/provider capability declarations, versions, permissions, environment, policy and rollback support.
4. **Scan cognitive readiness:** Check context freshness and coverage, assumptions, contradictions, domain uncertainty, dependency model, task complexity, evidence quality and verifier independence.
5. **Log gaps:** Write each gap as an append-only event through the existing ProgressJournal and reference its evidence in the canonical evidence envelope. Do not create a parallel gap database.
6. **Choose a disposition:** Proceed, research, POC, request approval/input, or block. Research and POC are conditional, not mandatory ceremony.
7. **Plan:** Build/update the canonical TaskPlan and StateGraph. Include gap owners, dependencies, budgets, evidence requirements, rollback owner and next safe action.
8. **Execute:** The canonical supervisor and ExecutionController remain authoritative for dispatch, budgets, attempts, deadlines, cancellation and provider routing. Use RuntimeExecutionGuard for consequential actions.
9. **Verify:** Evaluate acceptance evidence against the exact plan/intent/revision. Separate implementer and verifier for consequential work. Unknown, stale, missing or contradictory evidence blocks completion.
10. **Reconcile and learn:** Reconcile journal, recovery ledger, task/run state and delivery receipts. Admit learning only from eligible evidence; preserve negative, failed, cancelled and unscored outcomes.
11. **Report:** Derive dashboard status from durable state and evidence, not model-generated summaries. Record completed work, open gaps, blocked dependencies and the next safe action.

### 2.2 Three-axis readiness check

| Dimension | Questions AUREN must answer | Typical gap examples |
|---|---|---|
| **Technology (TECH)** | Is the required capability implemented and enabled? Are APIs/providers available? Are versions compatible? Are permissions, workspace, network and secrets bounded? Can the change be tested, observed and rolled back? | Missing provider lookup/idempotency; unsupported tool; incompatible schema; no test fixture; no host isolation; absent rollback adapter |
| **Intent (INTENT)** | What outcome is required? What is explicitly out of scope? Who has authority? What are the acceptance tests, risk tier, constraints and deadline? Are requirements ambiguous or contradictory? | Vague completion condition; conflicting instructions; missing approval; undefined target environment; acceptance criteria absent |
| **Cognitive (COGNITIVE)** | Is the context current and sufficient? Are assumptions visible? Are dependencies and failure modes understood? Is uncertainty calibrated? Is there a separate verifier/oracle? Can the active model/agent reliably reason over the task? | Stale memory; missing repository context; unsupported causal assumption; hidden dependency; insufficient reasoning capacity; evaluator shares candidate bias |

AUREN must not collapse these dimensions into one readiness score. A high score in one dimension cannot offset a blocking gap in another.

### 2.3 Gap severity and disposition

- **P0 / blocking:** Safety, authority, intent, data integrity, irreversible side effect, missing required evidence or unknown outcome. Stop the affected action; preserve state; escalate to the named owner.
- **P1 / high:** A material technical or reasoning uncertainty that can change architecture, correctness or delivery. Research or POC is normally required before implementation.
- **P2 / medium:** Bounded uncertainty with a safe workaround. Record and schedule remediation; proceed only if the plan explicitly accepts the risk.
- **P3 / low:** Improvement or non-blocking documentation/ergonomics issue. Record in the tracker; do not interrupt safe delivery.

Each gap record must include: `gap_id`, stable work key, work-unit/task ID, plan and intent digests, dimension (TECH/INTENT/COGNITIVE), severity, description, evidence references, confidence/uncertainty, impact, department/owner, remediation steps, acceptance test, timebox, state, blocker, next action, and resolution evidence. Resolution is a new journal event; prior gap events are never overwritten.

Recommended event types: `gap.detected`, `gap.triaged`, `gap.action_started`, `gap.blocked`, `gap.resolved`, `gap.reopened`. These are event names in the existing journal, not a new event store.

## 3. Research and POC timebox policy

### 3.1 Default budget

| Phase | Default cap | Rules |
|---|---:|---|
| Intake and intent normalization | 10 min | One pass; ask for clarification only when an essential decision cannot be safely inferred |
| TECH / INTENT / COGNITIVE gap scan | 20 min | Inspect existing source and evidence before searching or experimenting |
| Targeted research | 45 min | Only for freshness, unknown API/provider behavior, major design uncertainty or a material external dependency |
| Minimal POC | 90 min | One vertical slice, isolated workspace, synthetic/non-sensitive data, no production credentials or irreversible actions |
| Evaluation and decision | 25 min | Compare with baseline; record result, uncertainty, cost, failure modes and next action |

**Default total ceiling: 190 minutes per work unit.** Research is at most 45 minutes and POC at most 90 minutes. Small work can and should use lower caps. If the gap is already answered by current, trustworthy evidence, skip research. If an existing deterministic test is sufficient, skip the POC.

### 3.2 Hard limits

- At most one bounded research pass and one POC per work unit in basic mode.
- At most one POC correction iteration, only when the first result identifies a specific fix with a measurable expected benefit.
- At most one plan revision in the basic cycle. Repeated no-progress replanning is a stop condition.
- No automatic time extension. Expiry produces a decision record, unresolved gaps, preserved artifacts and a next safe action.
- Work expected to exceed the ceiling must be split into independently accepted child work units under the same parent stable work key. Do not reset budgets by renaming or restarting work.
- Research must answer a named question and produce source/evidence references. POC must test a named hypothesis with a baseline, success threshold and rollback.
- A POC is not production implementation, deployment approval, independent evaluation or permission to promote a model, harness, extension or provider.
- Do not install, grant authority to, or invoke newly discovered tools or extensions during discovery without explicit policy/approval.

### 3.3 POC decision record

Every POC records: hypothesis; gap IDs; baseline; candidate; isolated environment; exact source/plan/tool/model versions; allowed actions; budget; test data; metrics; observed result; negative results; limitations; security findings; rollback; decision (adopt, revise once, reject, or defer); and evidence references.

Adoption requires measured value against a baseline, passing regression/security checks, independent evaluation where the risk tier requires it, and a tested rollback. No default promotion.

## 4. Functional requirements (FR)

| ID | Requirement | Acceptance condition |
|---|---|---|
| FR-001 | Stable work identity | Create/resume by stable work key; repeated request does not create duplicate active execution |
| FR-002 | Intent contract | Goal, acceptance, scope, exclusions, authority, risk and deadline are explicit before consequential execution |
| FR-003 | Technology readiness scan | Inspect code, provider/tool contracts, versions, permissions, environment, tests and rollback capability |
| FR-004 | Cognitive readiness scan | Record context freshness, assumptions, contradictions, uncertainty, dependencies and verifier availability |
| FR-005 | Three-axis gap assessment | TECH, INTENT and COGNITIVE are assessed independently; blocking gaps cannot be averaged away |
| FR-006 | Durable gap log | Append gap lifecycle events to ProgressJournal and bind evidence references to the evidence envelope |
| FR-007 | Department ownership | Every actionable gap has one accountable existing owner, supporting roles, due/timebox and escalation path |
| FR-008 | Remediation path | Each gap has a next action, dependency, acceptance test, rollback/containment and resolution evidence |
| FR-009 | Conditional research | Research runs only when a named uncertainty requires current external or domain evidence |
| FR-010 | Bounded POC | POC is isolated, hypothesis-led, measurable, timeboxed and non-production by default |
| FR-011 | Canonical planning | TaskPlan and StateGraph express dependencies, gap tasks, acceptance, budgets, approvals and rollback |
| FR-012 | Single supervisor authority | ExecutionController/RuntimeExecutionGuard control dispatch, attempts, budgets, provider routing and side effects |
| FR-013 | Durable progress and recovery | Checkpoints, ProgressJournal and recovery ledger reconcile after restart without implicit replay |
| FR-014 | Cancellation and deadlines | Cancellation/deadline propagate to child work and providers; uncertain termination is reported as unknown/blocked |
| FR-015 | Side-effect safety | Persist intent before dispatch; require idempotency/authoritative lookup where supported; block unknown outcomes |
| FR-016 | Evidence-bound completion | Completion requires current, compatible, non-empty acceptance evidence bound to work/plan/revision |
| FR-017 | Independent verification | Consequential work cannot be approved by its implementer or self-asserted worker |
| FR-018 | Cross-store reconciliation | Split writes across journal, task state, recovery ledger, learning and delivery are reconciled deterministically |
| FR-019 | Evidence-gated learning | Only eligible verified outcomes become positive lessons; retain negative, failed, cancelled and unscored outcomes |
| FR-020 | Durable dashboard projection | Status, gaps, owners, timeboxes and next actions derive from durable records, not model prose |
| FR-021 | Departmental gap routing | Route to Product/Intent, Architecture, Engineering, Runtime/Platform, Security, QA, Data/Knowledge, Research, Learning, Delivery or Operations owner |
| FR-022 | Human approval and escalation | Ask the authorized operator for decisions that cannot be inferred; preserve pending approval across restart |
| FR-023 | No-progress termination | Stop on exhausted budget, repeated failure signature, stale plan, conflicting adoption or no measurable progress |
| FR-024 | Rollback and containment | Every runtime-affecting change identifies rollback owner, trigger, preserved state and recovery evidence |
| FR-025 | Task tracker | Every work item has priority, owner, dependencies, estimate, timebox, state, acceptance evidence and next safe action |
| FR-026 | Honest status | Distinguish proposed, implemented, CI-verified, deployment-verified and independently verified; skipped checks are not passes |
| FR-027 | Bounded multi-agent work | Parallel work uses isolated workspaces, explicit child identity, shared budgets and serialized adoption |
| FR-028 | Safe handoff | Handoff preserves intent, policies, approvals, failures, evidence gaps, budgets, checkpoint compatibility and rollback instructions |

## 5. Non-functional requirements (NFR)

| ID | Quality | Requirement / measurable acceptance |
|---|---|---|
| NFR-001 | Durability | A process restart must recover the last compatible checkpoint and reconcile all durable stores; no silent loss of intent, budget, approval or evidence |
| NFR-002 | Idempotency | Duplicate work keys and duplicate dispatches are detected; charged side effects are never blindly replayed |
| NFR-003 | Safety | Missing authority, policy, evidence, provider recovery or rollback for a consequential action fails closed |
| NFR-004 | Boundedness | Basic-mode research/POC caps and attempt/tool/token/cost/wall-clock budgets are enforced by the supervisor, not prompts |
| NFR-005 | Cancellation | Cancellation is durable, propagated and observable; non-cooperative work has a host-enforced termination policy or remains termination-unknown |
| NFR-006 | Integrity | Plan, intent, checkpoint, provider operation and evidence digests are verified; stale or incompatible state cannot be adopted |
| NFR-007 | Auditability | Every gap, decision, dispatch, approval, verification, learning admission and rollback has actor, timestamp, identity and evidence references |
| NFR-008 | Provenance | Research and learning retain source, revision, model/provider/tool, environment, baseline and evaluator provenance |
| NFR-009 | Observability | Operators can see lifecycle state, blocker, owner, elapsed/remaining budget, last progress, evidence and next safe action |
| NFR-010 | Truthful status | Unknown, skipped, stale, contradictory, unscored or missing results never appear as passed/complete |
| NFR-011 | Security | Least privilege, isolated workspaces, bounded network/credential access, action allowlists and host-enforced controls |
| NFR-012 | Privacy | Sensitive inputs are minimized, redacted from logs and never copied into research/POC artifacts without authorization |
| NFR-013 | Reliability | Recovery, cancellation, timeout, split-write, duplicate-dispatch and stale-writer fault injection is deterministic and repeatable |
| NFR-014 | Compatibility | Checkpoint/schema evolution is versioned; incompatible resume blocks or forks safely without mutating the prior run |
| NFR-015 | Performance | Gap scanning is proportional to task risk/complexity and reuses cached, revision-pinned evidence; avoid full-repository scans for trivial tasks |
| NFR-016 | Cost efficiency | Research/POC spend is measured; routing considers task capability, privacy, quality, latency, cost and provider health |
| NFR-017 | Portability | Basic operation works without requiring a single external model, IDE, provider, cloud or orchestration framework |
| NFR-018 | Interoperability | Adapters expose typed capability, identity, cancellation, evidence, idempotency and lookup contracts |
| NFR-019 | Maintainability | Reuse canonical runtime owners; no duplicate orchestrator, ledger, dashboard state machine or competing memory store |
| NFR-020 | Testability | Each requirement maps to unit, integration, fault-injection or operator acceptance evidence |
| NFR-021 | Usability | Every blocked/waiting state explains why, who owns it, what is needed and the next safe action |
| NFR-022 | Accessibility | Dashboard gap/status information is readable by keyboard and assistive technologies; state is not conveyed by color alone |
| NFR-023 | Scalability | Bounded fan-out and per-work-unit budgets prevent one task from exhausting shared capacity |
| NFR-024 | Rollback safety | Runtime changes are reversible or have an explicit containment plan; prior state and evidence are retained until acceptance |
| NFR-025 | Independent assurance | Deployment and independent-evaluation claims require evidence from outside the evaluated trust domain |

## 6. Functional departments and accountability

These are logical ownership lanes mapped to existing AUREN components and roles, not a new agent hierarchy.

| Department / lane | Accountable concern | Existing AUREN owners / interfaces |
|---|---|---|
| Product and Intent | Outcome, scope, acceptance, priority, user constraints | Goal System, TaskPlan, decision/approval gates |
| Architecture | Technical fit, contracts, dependencies, compatibility | Architecture, StateGraph, plan/schema owners |
| Engineering | Code and tests, maintainability, regression repair | Specialist crews, isolated workspaces, PRs |
| Runtime and Platform | Execution lifecycle, checkpoint, budget, recovery, cancellation | AutonomousCompany, ExecutionController, RuntimeExecutionGuard, recovery ledger, ProgressJournal |
| Security and Guard | Identity, authority, least privilege, host boundary, policy | Guard, ActionGovernance, ExecutionBoundary, approval store |
| QA and Verification | Test strategy, evidence quality, independent review | Verification/evidence envelope, CI, Arena interfaces |
| Data and Knowledge | Source freshness, provenance, contradiction, retention | Episodic/Semantic/Engineering Memory, Evidence Graph |
| Research and Capability | External research, benchmark selection, POC hypothesis | #1038 radar, capability matrix, research adapters |
| Learning and Evaluation | Experiment quality, causal attribution, admission/rollback | #692, learning pipeline, Arena |
| Delivery and Release | PR, exact-head CI, artifacts, release authorization | #694, GSD delivery, CI, portable bundle |
| Operations and SRE | Deployment, operator evidence, incidents, migration and rollback | #534, #909, #1193, deployment/host owners |

Every gap has exactly one accountable owner. Multiple departments may contribute, but responsibility cannot be ambiguous. AUREN may assign an agent persona to a lane only when its capabilities and permissions are verified.

## 7. Required status model

Use durable states consistently:

- `queued`: accepted but not started.
- `running`: active work with a valid lease/heartbeat.
- `waiting`: awaiting a named external event, CI, approval or provider response.
- `blocked`: cannot proceed safely; blocker and owner are mandatory.
- `failed`: terminal failure with evidence and remediation/rollback state.
- `cancelled`: cancellation acknowledged and side effects reconciled.
- `termination_unknown`: host cannot prove work stopped; no conflicting retry/adoption.
- `complete`: acceptance evidence and required verification are current and durable.

Dashboard status must be a projection of the canonical work-unit/plan/journal/evidence state. A worker saying "done" is not a state transition.

## 8. Implementation tracker and timeboxed delivery

The machine-readable tracker is `benchmarks/engineering_execution_tracker.v1.json`. It records task IDs, owners, dependencies, estimates, timeboxes, acceptance tests, current status and external blockers.

Initial delivery sequence:

1. Baseline and map existing canonical owners; identify duplicate or missing implementations.
2. Add typed gap events and a versioned gap-record contract to existing journal/evidence paths.
3. Add the three-axis readiness assessment before planning/dispatch.
4. Add bounded research/POC budget accounting to the canonical supervisor.
5. Add department ownership, remediation and escalation to TaskPlan.
6. Derive dashboard gap/status views from durable state.
7. Add integration/fault-injection tests across restart, cancellation, cross-store reconciliation and false completion.
8. Benchmark the basic mode against a no-gap-scan baseline; measure time-to-first-safe-action, gap detection, missed blockers, false blockers, cost and completion quality.
9. Roll out behind an explicit profile/feature flag with a rollback path; do not change default model/provider routing as part of this slice.

**No runtime implementation is claimed by this specification alone.** Each implementation slice requires its own isolated branch/PR, manual diff review, exact-head CI, and post-merge verification. No local checkout was available for this specification pass, so no local tests are claimed.

## 9. External gates that remain separate

- **#534:** Operator-controlled staging migration, fresh-process resume, rollback and post-cutover reconciliation receipts remain required.
- **#1256 / provider recovery:** #1305 is merged into main, but provider-native behavior and live failure-injection evidence remain external.
- **#1055 / #909 / #1193:** Identity, production PostgreSQL/host identity and multi-host deployment evidence remain separate deployment gates.
- **#404:** Independent evaluator, sealed holdout custody, external signing authority and retained campaign results remain required.

Internal CI may verify repository contracts. It does not prove production isolation, operator rollback, provider behavior, independent evaluation or AGI/SI capability.

## 10. Definition of done

Basic mode is accepted only when:
- Every work unit is assigned a stable identity and intent contract.
- TECH, INTENT and COGNITIVE are independently assessed and all detected gaps are durably logged.
- Each actionable gap has one owner, a remediation path, acceptance evidence and timebox.
- Research/POC are skipped when unnecessary and bounded when used.
- The canonical supervisor enforces authority, budgets, cancellation, recovery and no-progress termination.
- Completion and learning are evidence-gated.
- Dashboard state is derived from durable records.
- Fault-injection and regression tests pass on the exact PR head.
- Deployment/independent gates remain explicitly unverified until external evidence is independently checked.
