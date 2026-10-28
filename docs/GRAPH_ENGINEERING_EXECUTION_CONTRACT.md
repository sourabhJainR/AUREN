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

## Status — repository audit 2026-10-28

Audit baseline: `main` at `cb0848f71e66dd3ca3ad6157cd2633d4d85b40e6`, including the merged graph recovery and shared runtime/learning/delivery regressions. This is a repository/CI status, not a production or independent-evaluator certification.

- **Gate 1 — dependency-aware scheduling:** complete at repository level in [PR #1008](https://github.com/sourabhJainR/AUREN/pull/1008). The canonical TaskPlan/StateGraph path remains the only scheduler.
- **Gates 2–3 — isolation, adoption and verification:** implementation and regression coverage are merged in [PR #1009](https://github.com/sourabhJainR/AUREN/pull/1009), [PR #1010](https://github.com/sourabhJainR/AUREN/pull/1010), [PR #1011](https://github.com/sourabhJainR/AUREN/pull/1011), [PR #1026](https://github.com/sourabhJainR/AUREN/pull/1026), and [PR #1033](https://github.com/sourabhJainR/AUREN/pull/1033). Tests reject contradictory receipts and verifier self-approval before adoption or test-command execution.
- **Gate 4 — durable recovery matrix:** repository-level crash/restart and uncertainty cases are covered:
  - checkpoint committed before progress-journal append: [PR #1011](https://github.com/sourabhJainR/AUREN/pull/1011);
  - terminal journal event before recovery-ledger completion: [PR #1024](https://github.com/sourabhJainR/AUREN/pull/1024);
  - bounded, read-only checkpoint integrity audit: [PR #1025](https://github.com/sourabhJainR/AUREN/pull/1025);
  - contradictory verifier evidence: [PR #1026](https://github.com/sourabhJainR/AUREN/pull/1026);
  - process death after apply but before finalization: [PR #1029](https://github.com/sourabhJainR/AUREN/pull/1029);
  - cancellation and uncertain worker termination across restart: [PR #1030](https://github.com/sourabhJainR/AUREN/pull/1030);
  - verifier self-approval rejection: [PR #1033](https://github.com/sourabhJainR/AUREN/pull/1033);
  - stale intent/target HEAD and overlapping adoption rejection: [PR #1035](https://github.com/sourabhJainR/AUREN/pull/1035) and [PR #1036](https://github.com/sourabhJainR/AUREN/pull/1036).
- **Gate 5 — evidence-gated learning:** merged in [PR #1006](https://github.com/sourabhJainR/AUREN/pull/1006). Learning remains advisory; it cannot change authority, evidence thresholds or rollback policy.
- **Cross-store completion and duplicate dispatch:** the shared-runtime integration and terminal-receipt crash regressions are merged in [PR #1034](https://github.com/sourabhJainR/AUREN/pull/1034) and [PR #1037](https://github.com/sourabhJainR/AUREN/pull/1037). They bind the stable work key `AUREN:issue-696:continuous-engineering`, require reconciliation before a completed dashboard projection, and prevent replay after ambiguous split writes.

### Current-main verification snapshot

- Main SHA: `cb0848f71e66dd3ca3ad6157cd2633d4d85b40e6`.
- Current-main check-runs: 28 completed successfully. Six push workflows are completed successfully; this status does not convert any path-filtered skip on a historical PR head into a pass.
- Retained portable artifacts from [run 40552519697](https://github.com/sourabhJainR/AUREN/actions/runs/40552519697) expire on 2026-11-26. GitHub outer digests matched the downloaded archives: bundle archive `c857cf81e5ffca7592f5845d76bf5dee774191b8fa29f6004310e2f752152bf5`; checksum archive `d23f06546b5c025171ea72b7e30b8aa5bf351a5517bd8c874008e68d330f11bc`. The embedded portable ZIP SHA-256 `2f40bf4aa96e6ba61d5ed8d6a47b6c8e0faba09f8c77813b6fe0c845c99cb495` matches `auren-portable.sha256`; all 508 manifest entries match their sizes and SHA-256 values. The manifest pins the audited main SHA, version `1.0.6`, and `target_repository_mutation=false`.
- No local test run is claimed because this environment has no AUREN checkout. Verification here uses merged PR records, current-main check-runs, push workflow records and downloaded artifact hashes.

### Explicitly unverified external gates

Repository-level implementation and CI do **not** prove production OS/network/credential isolation, independent evaluator identity, sealed-holdout custody, provider-native idempotency or real staging rollback. Those remain blocked until genuine operator-controlled or independently verifiable evidence is attached to their owning issues. Do not describe the graph worker's fresh worktree as a host sandbox.

### Adoption failure recovery and rollback

The adoption boundary is considered uncertain as soon as the coordinator is entered. A failed return may include an interrupted rollback, so the worker lifecycle records the affected node as unknown, blocks the graph run, and corrects any terminal progress projection to blocked unless the node's durable completion evidence already agrees with the recovery ledger. A cleanup or recovery-ledger commit failure after file application is not a successful task and must not be retried automatically. Siblings whose cancellation was requested remain unknown/blocked until the host confirms they have stopped.

Before the coordinator applies any patch, each verified node must transition from `working` to durable `adopting` and append an `adoption-started` journal event with proposal and receipt references. If the process dies between the recovery checkpoint and the progress-journal append, the checkpoint remains authoritative for dispatch safety: the node is still `adopting`, and restart must not redispatch it because the progress event is absent. If the process dies after apply but before finalization, the node remains `adopting`, the progress event remains nonterminal, and the node is not dispatchable. A separate crash can occur after the progress journal fsyncs a terminal `completed` event but before the recovery ledger commits node completion. On restart, if the durable stores disagree, the supervisor must mark the node `unknown`, block the run, and append a sequence-fenced reconciliation event that changes the progress projection to `blocked`. It must not replay the worker or accept the terminal journal event alone as proof of completion. A dirty canonical checkout is rejected on restart. All crash boundaries require explicit reconciliation of the target tree, progress journal, recovery ledger, proposal and receipt before any further action; neither boundary authorizes automatic replay.

For recovery, preserve the canonical checkout, source revision, proposal patches, verification receipts, progress journal, and recovery ledger. Compare the target tree with the recorded base and proposed path set, verify each receipt digest and workspace state, and reconcile the journal and recovery ledger before resuming. If rollback is needed, use a reviewed rollback against the exact recorded base and verify the resulting tree is clean. Never reset a blocked/unknown/adopting task to pending or replay its worker to make the run green.

Do not treat this contract as proof of production isolation. Gate completion requires passing exact-head tests, truthful skipped-check reporting, post-merge main verification, and explicit evidence for every external dependency.
