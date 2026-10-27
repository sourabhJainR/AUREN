# Graph Engineering Execution Contract

Status: implementation contract for issue #998. This document describes required behavior; it is not evidence that every gate is already implemented.

## Canonical owners

- Task dependencies and stable plan identity: `portable.task_planner.TaskPlan`.
- Graph scheduling and parallel supersteps: the existing `portable.agency_state_graph.StateGraph`, called by `.ai-harness/runtime/graph_agent_team.py`.
- General orchestration: `portable.adaptive_runtime.AdaptiveRuntime` and `portable.orchestration.Graph/Orchestrator`.
- Durable task lifecycle: `portable.execution_controller.ExecutionController`, supervisor recovery ledger, and progress journal.
- Shared runtime limits: the supervisor-owned `RuntimeExecutionGuard`.
- Specialist proposal dispatch: `portable.state_graph.GraphAgentTeam` and `portable.bounded_crew_dispatch.BoundedCrewDispatcher`.
- Evidence and promotion: canonical evidence envelope, Guard/Arena, exact-head CI, decision record, and rollback gates.

Do not introduce a second planner, scheduler, retry ledger, evidence store, memory store, or promotion authority.

## Scheduling rules

1. Derive graph edges and prerequisite checks from the canonical `TaskPlan`; reject cycles and missing dependencies before dispatch.
2. Bind the run to a stable TaskPlan definition digest and the exact intent digest. Resume rejects a changed digest before dispatch; task status and execution output do not alter the plan digest.
3. Use the existing `StateGraph` scheduler for independent, read-only supersteps. Run a single eligible node inline; when duration estimates exist, fan out only if estimated savings exceed configured overhead. Do not add another scheduler or executor.
4. Keep local commands and the learning steward out of parallel fan-out. SharedTaskMemory file writes and persistent learning-experience writes are serialized; shared-resource mutations, evidence finalization, and adoption remain serialized.
5. Enforce one task-wide concurrency, deadline, attempt, and resource budget across nested graph and crew boundaries.
4. If the task is narrow or sequential, execute the chain without adding parallel coordination overhead.
5. On a hard safety failure or exhausted task budget, stop scheduling new work, request cancellation of siblings, and record any non-preemptible worker as still running/unknown until confirmed stopped.

## Isolation and adoption

- Workers that modify files must use separate worktrees or equivalent isolated workspaces with explicit file ownership.
- A worker's proposal is not permission to modify the canonical workspace, merge, promote, or approve itself.
- Adoption is serialized and must check the base revision, artifact digest, declared file scope, and conflict status immediately before applying changes.
- Stale-base, overlapping-write, failed-cleanup, or unresolved-conflict outcomes block adoption; they must not be reported as successful.

## Independent verification

- A verifier uses fresh context and a separate role/identity from the worker where the environment supports it.
- Verification must inspect concrete artifacts and independent signals, such as executed tests, static-analysis output, or reproducible checks. Worker claims and model confidence are not proof.
- Bind the verification result to the task/intent digest, source revision, artifact digest, verifier identity, command/check identity, and result.
- Missing, stale, contradictory, skipped-but-required, or unscored evidence cannot pass the gate.

## Durable state and recovery

Persist task/node identity, dependency state, worker and workspace identity, attempt/budget snapshot, source revision, artifact/evidence references, and the next safe action at durable boundaries. On restart, reconcile the plan store, recovery ledger, progress journal, and provider receipts before resuming. Conflicting or ambiguous state is blocked; do not replay consequential side effects without provider-native idempotency or status reconciliation.

## Learning and cost control

Record graph topology, concurrency, model/provider, quality, regression, latency, cost, retries, and independent verification outcomes. Compare candidates against a pinned baseline using the same task set and preserve holdouts. Learning may recommend graph topology or worker selection changes; it must never relax authority, safety rules, evidence requirements, verification thresholds, or rollback gates.

## Required regression matrix

- Independent read-only nodes overlap in the existing StateGraph runtime; dependent nodes wait.
- TaskPlan digest is stable across status/output changes and changes when dependencies or execution scope changes.
- Resume rejects stale task-plan or intent digests; cycles, missing prerequisites, and failed dependencies are rejected or block downstream work.
- Nested graphs cannot reset the shared worker, deadline, retry, or resource budget.
- Worker failure stops new dispatch and records cancellation/termination uncertainty truthfully.
- Separate workspaces prevent shared-file races; adoption is serialized and stale-head guarded.
- Fresh-context verification rejects self-attested success and missing/false test evidence.
- Crash/restart, duplicate dispatch, partial receipts, cancellation, timeout, and ambiguous provider outcomes never create false success.
- Exact-head required CI passes; skipped checks are reported as skipped, not passed.
- Production isolation and independently controlled evaluator/holdout custody remain open until external evidence is supplied.

## Status

- Gate 1 is merged in PR #1008. The exact PR head passed all 11 workflows, including architecture, runtime resilience, Windows/Linux, production hardening, and the integration guard.
- Gates 2–3 have existing isolated-worktree and fresh-worktree verifier primitives. PR #1009 merged at `eb53cd77554a74d31ad54c3b7202957f2e07d465`; its exact reviewed head passed all eight PR workflows and all eight post-merge workflows passed on main. The slice adds a StateGraph node adapter, one serialized sibling-adoption boundary, receipt persistence, and progress-journal integration.
- Gate 4 remains partial. The shared RuntimeExecutionGuard, checkpoints, recovery ledger, and ProgressJournal block duplicate dispatch after ambiguous prior state. This follow-up adds a durable `adopting` state before filesystem application and a subprocess crash regression for the apply-to-finalization boundary. Cleanup-failure, recovery-commit, sibling-cancellation, and crash recovery are repository-level contracts; supervisor reconciliation and external isolation evidence remain outstanding.

- Gate 5 is merged in PR #1006. Its fixed safety gates and pinned-baseline comparison remain authoritative; learning is advisory only.
- The fresh-worktree verifier proves reproducible checks in a separate worktree, not external evaluator custody or OS sandbox resistance. Those claims remain open until independent operator-controlled evidence is supplied.

### Adoption failure recovery and rollback

The adoption boundary is considered uncertain as soon as the coordinator is entered. A failed return may include an interrupted rollback, so the worker lifecycle records the affected node as unknown, blocks the graph run, and corrects any terminal progress projection to blocked unless the node's durable completion evidence already agrees with the recovery ledger. A cleanup or recovery-ledger commit failure after file application is not a successful task and must not be retried automatically. Siblings whose cancellation was requested remain unknown/blocked until the host confirms they have stopped.

Before the coordinator applies any patch, each verified node must transition from `working` to durable `adopting` and append an `adoption-started` journal event with proposal and receipt references. If the process dies between the recovery checkpoint and the progress-journal append, the checkpoint remains authoritative for dispatch safety: the node is still `adopting`, and restart must not redispatch it because the progress event is absent. If the process dies after apply but before finalization, the node remains `adopting`, the progress event remains nonterminal, and the node is not dispatchable. A separate crash can occur after the progress journal fsyncs a terminal `completed` event but before the recovery ledger commits node completion. On restart, if the durable stores disagree, the supervisor must mark the node `unknown`, block the run, and append a sequence-fenced reconciliation event that changes the progress projection to `blocked`. It must not replay the worker or accept the terminal journal event alone as proof of completion. A dirty canonical checkout is rejected on restart. Both crash boundaries require explicit reconciliation of the target tree, progress journal, recovery ledger, proposal and receipt before any further action; neither boundary authorizes automatic replay.

For recovery, preserve the canonical checkout, source revision, proposal patches, verification receipts, progress journal, and recovery ledger. Compare the target tree with the recorded base and proposed path set, verify each receipt digest and workspace state, and reconcile the journal and recovery ledger before resuming. If rollback is needed, use a reviewed rollback against the exact recorded base and verify the resulting tree is clean. Never reset a blocked/unknown/adopting task to pending or replay its worker to make the run green.

Do not treat this contract as proof of production isolation. Gate completion requires passing exact-head tests, truthful skipped-check reporting, post-merge main verification, and explicit evidence for every external dependency.
