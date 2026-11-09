# AUREN implicit state-machine audit

**Audit date:** 2026-11-09  
**Scope:** dashboard and API, durable execution, plans, agent runtime, provider operations, approvals, workspaces, learning, delivery and release.  
**Decision:** preserve domain-owned state machines; standardize their read-only lifecycle projection.

## Executive finding

AUREN does not have one hidden state machine. It has multiple legitimate, domain-specific state machines plus several implicit projections that reinterpret those states in the UI, dashboard and recovery code.

The risk is not that there are many state machines. The risk is that the same work item can be described differently by its checkpoint, progress journal, controller task row, plan run, dashboard projection and browser filter. Those representations must not become competing authorities.

The most concrete UX inconsistency found in this audit was in session tracking:

- `MuxinSessionAdapter._session_record()` inferred status from checkpoint stage, last error and heartbeat age.
- It collapsed failed, cancelled and blocked sessions into the same `blocked` status.
- The browser grouped failed and cancelled records into the Blocked filter.
- The backend's active filter and the browser's active filter used different state sets.
- Task, plan and workspace cards displayed raw status strings rather than a shared interpretation.

That makes it harder for a user to tell whether work failed, was cancelled, is waiting, or needs recovery. It also gives agents and developers inconsistent signals.

## Inventory

The following inventory distinguishes an authoritative state owner from a projection or coordinator. A projection must never write state or authorize a transition.

| Area | State machine / implicit lifecycle | Current owner or source | Finding |
|---|---|---|---|
| Browser sessions | Snapshot, busy, refresh-again, search, filter and pagination | `dashboard/assets/sessions.js` | Local UI state is implicit and independently interprets status strings. |
| Live operations panel | In-flight request, last successful sample, timeout, offline and degraded states | `dashboard/assets/control-center-live.js` | A second polling lifecycle exists beside the sessions panel. It is observational, not execution authority. |
| Session checkpoints | Stage, attempt, provider, completed/remaining batches and heartbeat | `portable/session_state.py` | Checkpoints do not contain a separate canonical session status; the adapter derives one. |
| Session projection | Running, waiting, complete, blocked, stale or unverified | `portable/muxin_session_adapter.py` | Stage/error/age inference is useful for display but must be marked as derived. |
| Supervisor progress | Queued, running, blocked, recovering, completed, failed and cancelled | `portable/progress_journal.py` | Append-only progress authority. Blocked and recovering are not terminal; completed, failed and cancelled are terminal. |
| Durable task execution | Admission, claim/lease, dispatch, running, quota wait, approval wait, cancellation, recovery and terminal receipt | `portable/execution_controller.py` | The execution authority. Its state is coordinated across task rows, leases, progress events, approval and side-effect receipts. Do not duplicate its transition authority. |
| Engineering plan task | Pending, ready, running, verifying, passed, blocked, failed and cancelled | `portable/engineering_plan.py` | Explicit transition graph. Failed and blocked tasks can return to ready; passed and cancelled are terminal. |
| Engineering plan run | Planned, ready, running, completed, failed, blocked, cancelled or unknown | `portable/engineering_dashboard.py` plus persisted plan/run and checkpoint | A digest-checked dashboard projection. It must not override the persisted plan/run or checkpoint. |
| StateGraph execution | Node routing, evaluator, repair, retries, checkpoints and crew dispatch | `portable/state_graph.py`, `portable/agency_state_graph.py` | Graph topology and node execution form a state machine. Keep graph transitions inside the graph/supervisor contract. |
| Agency execution | Created, planned, executing, verifying, repairing, escalated, passed, failed and blocked | `portable/agency_state.py` | Explicit but separate lifecycle. It overlaps conceptually with plan and controller execution; it must not become a second source of task truth. |
| Agent turn | Idle, planning, acting, observing, verifying, deciding, repairing, stopped, completed and failed | `.ai-harness/runtime/agent_turn.py` | Explicit provider-neutral turn machine with live observation and interrupt decisions. |
| Graph worker | Pending, working, adopting, waiting, blocked, complete, failed, cancelled and unknown | `portable/graph_worker_lifecycle.py` and recovery bridge | Worker, workspace and recovery states must reconcile before redispatch. |
| Workspace | Creating, active, proposal-ready, adopting, merged/closed, failed or stale | Graph workspace registry/coordinator; projected by `MuxinSessionAdapter` | Host-owned lifecycle. Dashboard remains read-only. |
| Human approval | Pending, approved, executing, outcome-unknown, completed, failed, rejected, expired, cancelled and superseded | `portable/durable_approval.py` | Durable CAS transitions and audit chain. Approval state does not replace side-effect reconciliation. |
| Consequential side effect | Executing, completed, reconcile-required and retry-authorized | `portable/side_effect_ledger.py` | Idempotency and uncertainty boundary. An unknown result must be reconciled before retry. |
| Provider operation | Dispatching, completed, blocked or reconcile-required | `portable/provider_operation_lifecycle.py` over the side-effect ledger | Provider receipt and lookup lifecycle. A dispatch return is not proof of completion. |
| Response delivery | Queued, running, blocked, recovering, completed, failed and cancelled, plus chunk acknowledgement cursor | `portable/progress_journal.py` and `portable/dashboard_server.py` | Delivery completion requires receiver acknowledgement; late acknowledgements must not revive blocked or terminal work. |
| Evidence outbox | Pending, sending, sent, stale-claim recovery and retry | `portable/engineering_evidence_capture.py` | A separate delivery lifecycle for evidence. Stable event IDs and claim tokens prevent duplicate or stale writes. |
| Continuous goal execution | Running, required-check wait, main-head wait, poll error, unverified completion, hold, backoff, stop-requested and stopped | `portable/continuous_execution.py` | Long-running supervisor and CI polling machine. Observed completion is not verified completion. |
| Autonomous work unit | Created/running, waiting for CI or external input, completed, failed or cancelled | `portable/autonomous_company.py` | Durable work-unit lifecycle with separate event records and clarification states. |
| Goal planning | Open, active, blocked, completed and abandoned | `portable/goal_manager.py` | Durable goal hierarchy; goal completion is distinct from task execution completion. |
| Legacy task planning | Pending, in-progress, blocked, done and cancelled | `portable/task_planner.py` | Smaller task vocabulary. Treat as planning/compatibility input, not a competing execution authority. |
| Bounded feedback loop | Success, clean no-op, blocked, approval-required, exhausted, no-progress and error | `portable/feedback_loop.py` | Explicit terminal outcomes and finite pass budget. |
| Learning hypothesis | Observed, investigated, hypothesized, planned, evaluated, approved, deployed, verified, rejected, blocked, inconclusive and rolled back | `portable/agency_evolution_gates.py` | Evidence and independent review gate learning promotion. |
| Capability promotion | Canary, promotion-blocked, promoted and rolled back | `portable/capability_lifecycle.py` | Separate bounded canary/promotion state; promotion must remain evidence-gated. |
| Capability invention | Candidate and promoted, with benchmark/evidence gates | `portable/invention_lifecycle.py` | Candidate state is not a deployed capability. |
| Experiment queue | Queued, assigned, observed and attributed | `portable/experiment_queue.py` | Observation and attribution are distinct; queued work is not evidence. |
| Release channels | Staged artifact, shadow, canary, current/promoted and rollback | `portable/agency_release_lifecycle.py` and `portable/context_bound_release.py` | Release actions update channel pointers only after evidence gates. |
| CI and health | Poll result, required-check state, readiness, liveness and deployment evidence | `portable/continuous_execution.py`, `portable/dashboard_server.py` | HTTP liveness, local readiness, provider health and production readiness are separate claims. |

### Important non-equivalences

These states must remain distinct even when they share a visual category:

- `blocked` is recoverable in some domains and terminal in others.
- `failed` is retryable for an engineering plan task, but terminal in the progress journal and approval operation.
- `completed` in a delivery journal is not the same as a plan whose every task passed with evidence.
- `approved` authorizes a bounded operation; it does not prove that the operation ran or succeeded.
- `dispatching` or `executing` does not prove an external side effect completed.
- `outcome_unknown` and `reconcile_required` must never be displayed as ordinary failure or retried automatically.
- `stale` is an observation about freshness, not a terminal outcome.
- `observed_complete_unverified` is not completion.
- A session stage such as `testing` or `reviewing` is a phase, not a task status.

## Architecture decision

### 1. Keep domain-owned state machines

Do not replace these machines with one giant global FSM. Their transition rules, terminal states, retry rules, evidence requirements and authority boundaries differ.

Each domain keeps exactly one authoritative writer for its durable state. Other components read, reconcile or project that state. Cross-domain progress is communicated through stable IDs, events and evidence receipts—not by mutating another domain's status directly.

### 2. Add one shared read-only lifecycle projection

`portable/lifecycle_projection.py` defines a common envelope for user, developer and agent surfaces:

- `domain` and `source_state`: preserve the original domain vocabulary.
- `category` and `label`: provide consistent display and filtering.
- `terminal` and `attention_required`: make lifecycle meaning explicit.
- `resume_policy`: `never`, `reconcile_first` or `controller_decides`.
- `next_action`: a stable, machine-readable guidance identifier.
- `schema_version`: permits additive evolution.

The projection is intentionally non-authoritative. It cannot dispatch, retry, approve, complete or resume work. Unknown domains and states map to `unknown`, require attention and fail closed.

### 3. Preserve compatibility

The first integration is additive:

- Existing raw `status`, `state` and stage fields remain available.
- Session, task, plan and workspace API records gain a `lifecycle` object.
- Session failure and cancellation are now distinct from blocked.
- Session filters and badges use the same normalized category.
- Failed and cancelled counts and filters are exposed separately.
- Session state explicitly identifies that it is derived from checkpoint stage, error and heartbeat.

No persisted task, approval, provider, plan, workspace or release state is rewritten by this change.

### 4. Keep authority out of UX and AX

The dashboard may recommend a next action but cannot perform it. Agents may read `resume_policy` and `next_action`, but must still call the canonical controller, approval store or reconciliation path. A display label is never an authorization token.

## UX, DX and AX outcomes

| Experience | Improvement |
|---|---|
| UX | Failed, cancelled, blocked, stale, waiting and uncertain work are distinguishable. Users see what happened and the next safe action instead of one overloaded status. |
| DX | One documented projection contract reduces duplicated status mapping and makes new domain states testable. Raw domain states remain intact for debugging. |
| AX | Agents receive explicit terminality, attention and resume policy plus stable next-action identifiers. Unknown states cannot be mistaken for success or safe retry. |

## Verification requirements for future work

1. Every new durable state vocabulary must name its authoritative owner, legal transitions, terminal states, retry semantics and evidence requirements.
2. Every authoritative vocabulary must have a projection mapping or deliberately remain unknown.
3. Every transition must test valid paths, invalid paths, terminal-state behavior, restart/reconciliation and concurrent writers where applicable.
4. Cross-domain transitions must bind the same task/run/operation identity and source revision.
5. Unknown, stale, corrupt or conflicting state must never be converted to success.
6. A resume/retry action must be authorized by its owning controller, not inferred from a UI category.
7. Dashboard/API changes remain additive until consumers and stored-state compatibility are proven.

## Rollout boundary

The shared projection is now used by the sessions API for session, progress-task, plan-run and workspace records. The remaining state machines are inventoried but retain their existing domain-specific runtime and API contracts. Extend the projection to those surfaces incrementally with contract tests; do not mass-rewrite state storage or transition logic.

This is the safest path to consistent UX, DX and AX without breaking durable state, existing integrations or execution authority.
