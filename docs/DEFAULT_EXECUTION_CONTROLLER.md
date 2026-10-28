# AUREN Default Execution Controller

AdaptiveRuntime.run() is now the durable execution boundary for AUREN graph/team work.

## Default path

AdaptiveRuntime.run() -> ExecutionController -> AutonomousCompany -> existing Orchestrator(Graph).

This preserves the existing graph execution engine while making the supervisor the owner of task lifetime.

## Restart behavior

A session is mapped to a durable work unit. For a directly constructed `ExecutionController(root)`, its ledger is stored at `<root>/.auren/execution.sqlite3`. The default `AdaptiveRuntime` supplies `~/.auren/executive` as that root. On startup, `AurenStatePaths` copies known SQLite stores and session JSON from legacy `~/.auren` into matching canonical paths using atomic writes and SQLite backup. Legacy files are retained. If both roots contain conflicting data, startup fails closed with an actionable `StatePathConflict`; do not delete either copy before an explicit backup and reconciliation. See [issue #379](https://github.com/sourabhJainR/AUREN/issues/379) for migration rollout and live-environment verification.

If the worker exits after a work unit has started, the next invocation with the same session_id resumes the existing work unit rather than creating a new one.

For direct task submission, pass a stable `work_key` to `ExecutionController.submit(...)` (or include it in the payload). The controller stores the key on the task and passes it to `AutonomousCompany.start()`. Repeated submissions with the same key and identical handler, payload, and goal return the existing task and work unit. Reusing the key for different intent raises `WorkKeyConflict`; it never silently creates a second task. Keep the key stable across worker restarts and branch/session handoffs. The key is an identity, not authorization: current policy, approval, revision, and evidence gates still run before consequential actions.

## Task deadlines and cancellation

`ExecutionController.submit(..., deadline_seconds=N)` accepts an optional finite duration greater than zero and no more than 24 hours. The controller persists the resulting absolute deadline in the task row and restores it on restart. Re-submitting the same stable work key returns the original task and original deadline; it does not extend the deadline. Expired tasks fail without accepting a late handler result. The timestamp is timezone-aware UTC; malformed or timezone-naive persisted deadlines fail closed.

`ExecutionController.request_cancellation(task_id, reason=...)` persists a cancellation request and returns whether the task was eligible for a request. It does not immediately claim that work has stopped. The durable record stores only a SHA-256 digest of the supplied reason. Active handlers receive `auren_cancel_event`; handlers should check it during long-running work. The controller rejects a result returned after cancellation.

Command-backed handlers should use `SubprocessProviderInvoker.invoke_supervised(prompt, timeout_seconds, execution_payload=payload)`. This passes the controller's cancellation event into the killable subprocess boundary and caps the process timeout using `auren_deadline_epoch`. A handler that ignores the event cannot be forcibly stopped by the Python supervisor. For consequential work, cancellation or deadline expiry after dispatch leaves the task blocked for provider reconciliation if the remote outcome is uncertain; never manually reset it to pending.

## Wiring supervisor controls into runtimes

Handlers that invoke the checkpointable graph runtime should call `GraphAgentTeam.run_supervised(..., execution_payload=payload)`. This binds the controller's cancellation event to the shared runtime guard and tightens (never extends) an existing checkpoint deadline. The compiled graph API uses `CompiledStateGraph.invoke_supervised(state, execution_payload=payload)`; model routing uses `HybridModelRouter.route_supervised(request, execution_payload=payload)`. These adapters keep runtime-only objects out of JSON graph state and reject expired deadlines before dispatch.

An existing runtime guard must use the same cancellation event as the supervisor. A mismatch is rejected rather than silently creating a second cancellation domain. Cancellation remains cooperative for Python graph nodes and model callbacks; use `SubprocessProviderInvoker.invoke_supervised(...)` for command-backed work that must be terminated at the process boundary.

**Rollback:** retain the task database and progress/evidence journals. A code rollback does not clear cancellation flags or deadlines and must not replay a consequential operation whose provider outcome is unknown.


## Bounded worker output

The command `python -m portable.auren_runtime worker ... --json` returns the explicit `stop_reason` and a `task_state_counts` snapshot read from the same durable task-state snapshot used by the continuous-run report. The map includes every state currently present in SQLite, including blocked, approval-waiting, terminal, and unfamiliar states; it does not turn unknown states into success. An empty map means the task table has no rows.

`idle_budget_exhausted` means the worker found no processable work for the configured idle budget. It does not mean the project is complete. Exit code 2 means nonterminal tasks remain; inspect `task_state_counts` and the progress journal, resolve blockers, then resume with the same stable work key. The worker remains a bounded slice of the existing supervisor, not a second daemon or scheduler.

**Rollback:** the JSON field is additive. Consumers should ignore unknown fields and continue to use `stop_reason` and the existing exit code. If reverting this reporting change, revert code only; preserve the execution database, work-unit receipts, progress journal, and evidence ledger. Never reset a blocked task to pending to make the counts look clean.

Submission evidence is a projection of the canonical task row. The controller assigns a stable event ID ('task-submitted:<task-id>') and verifies the event before returning from submission. On startup, reconcile_submission_events() scans a bounded number of task rows and repairs missing submission events without invoking handlers or replaying side effects. Repeated submission with the same work_key also retries this projection repair. Existing, valid legacy submission events are retained. Multiple or mismatched events remain a health finding and are not overwritten. Progress is rebuilt from the authoritative task and executive receipts by the existing bounded reconcile_progress_journal() path; it appends a startup-reconciliation record rather than deleting history. If either projection cannot be verified, retry with the same stable key and inspect health findings. The combined #696 regression `test_integrated_task_row_receipt_split_blocks_plan_dashboard_and_never_replays` kills a child process after the task row commits terminal state but before the executive receipt; restart must block the task and plan, retain the verified learning record, and never replay the handler. For rollback, use a reviewed code revert, preserve all durable stores, and keep affected work paused until any missing audit projection is reconciled.

Terminal lifecycle evidence is written only after the task row and executive work-unit receipt agree on a terminal outcome. On startup, reconcile_terminal_events() repairs a missing terminal evidence event only when both durable states agree. Duplicate, malformed, or mismatched terminal events remain a health finding and are not overwritten. A missing audit projection does not authorize handler replay; a split task/receipt state remains blocked by progress reconciliation.

### Crash between task-row finalization and the executive receipt

A process can stop after the SQLite task row changes to a terminal state but before the AutonomousCompany work-unit receipt commits. Startup reconciliation must treat that split as an unknown outcome: change the canonical task row to `blocked`, append a `startup_reconciliation` progress event, and expose a health finding. It must not synthesize terminal evidence, report success, or rerun the handler. The regression test `test_startup_blocks_task_terminal_commit_before_work_unit_receipt_without_replay` injects process death at this boundary and verifies that the handler is not invoked again.

**Recovery/rollback:** preserve the SQLite database, executive work-unit receipt, progress journal, and evidence ledger. Do not delete the partial receipt or reset the task to `pending`. An operator must inspect the exact task/work-unit IDs and any consequential side-effect ledger record, reconcile the remote outcome with trusted evidence, and use a reviewed recovery or code revert. Keep the task blocked until the stores agree; a code rollback must never turn this split state into success.


Each task claim now advances a durable lease generation and receives a fresh fencing token. Heartbeats and terminal-state commits must match both values, so an expired/reclaimed worker cannot overwrite the newer owner's outcome. This does not undo a side effect already performed by a handler that outlives its lease; consequential handlers must use task-scoped idempotency keys at their external effect boundary. The supervisor now renews the lease in a separate heartbeat thread while a handler runs and verifies ownership again before finalization. If renewal fails, the handler result is rejected. This still cannot cancel an external side effect already in progress; such effects must use task-scoped idempotency keys.

The existing maintenance service calls resume_tasks() before its maintenance cycle. Its native service definitions already use restart-on-failure behavior, so host/process restart feeds back into the same durable queue.

## Truthful progress journal

`ExecutionController` writes append-only lifecycle events to `<root>/.auren/progress.jsonl` (the default `AdaptiveRuntime` uses the executive state root). SQLite remains authoritative for task state and work-unit receipts; the JSONL journal is the durable progress projection used by the dashboard. Events cover queueing, claim, preflight, handler dispatch, lease heartbeat, bounded quota wait, stale recovery, and terminal outcome. The journal reports `completed` only after the executive work-unit receipt commits. A journal write failure does not silently rewrite task state; it appears in `health_findings()` and the dashboard must show missing/unknown progress honestly. The dashboard checks the project-local journal and the canonical executive journal path. CLI journal inspection is available with `python auren_cli.py progress --json`; provider-specific subtask progress remains a separate integration gate.

## Durable handlers

Register an importable module-level handler with `runtime.register_task_handler(name, handler, execution_class=...)`. Every handler must be explicitly classified as `read_only`, `context_provider`, `ci_gated`, or `consequential`. Consequential handlers require both an operator-owned `promotion_gate_policy={"context_handler": "trusted-provider-name"}` and an exact-head `ci_gate_policy` with required checks and a separate trusted evidence provider. CI-gated handlers require exact-head CI. Context providers are internal-only and cannot be submitted as tasks. New and legacy-unclassified handlers fail closed until an operator reviews and explicitly classifies them. The class is persisted separately from task payload and cannot be downgraded by re-registration. Submit ordinary handlers with `runtime.submit_task(name, payload)` and resume with `runtime.resume_tasks()`. The context provider receives task identity only, not task payload, and must resolve repository/head/approver/canonical evidence from trusted live sources.

Handlers are stored as module:qualname, not serialized Python objects. This makes them reconstructable by a new process.

Each classified durable registration also stores a SHA-256 digest of the handler source. The controller recomputes it whenever the handler is loaded and refuses to run changed source under the same registered identity. This is an integrity check, not a signature: deployment provenance and OS-level protection of code and SQLite state remain necessary. Existing classified rows created before digest enforcement may have no digest; they fail closed and must be migrated under a new, reviewed handler identity rather than silently adopting the currently installed source. Back up the execution database and inventory gate policies before this schema upgrade.

## StateGraph / GraphAgentTeam compatibility

`portable.state_graph.StateGraph` and `portable.state_graph.GraphAgentTeam` are the supervisor-facing compatibility API over `portable.orchestration.Graph` and `Orchestrator`; `ExecutionController.execute_graph()` dispatches these types. Separately, `portable.agency_state_graph.StateGraph` is a lower-level checkpointable graph runtime with explicit state, edge, interrupt and bounded-superstep semantics. These are distinct classes in distinct modules and must not be treated as interchangeable without an explicit adapter and integration tests.

## Graph checkpoint startup visibility

`ExecutionController` audits the supervisor-owned `graph_checkpoints` table at startup and during bounded health checks. It validates the stored envelope digest, task binding, retry-budget agreement, runtime-guard snapshot, and provider-resume digest. A corrupt or malformed checkpoint is reported as `graph_checkpoint_integrity_required`; an incomplete scan is reported separately and is not treated as a clean result.

This audit is read-only. It does not reconstruct a checkpoint from current code, overwrite a damaged digest, mark a graph complete, or replay a worker. `load_graph_checkpoint()` continues to reject invalid bytes before graph resume. Restore a checkpoint only from a trusted backup and reconcile its task, evidence and provider outcome before resuming.

**Rollback:** revert the audit code only. Preserve `.auren/execution.sqlite3`, the progress journal, executive work-unit receipts, and evidence databases. Do not delete or reset a damaged checkpoint to make health findings disappear.

## Recovery

A failed graph execution is recorded as a supervisor failure. The existing default runtime permits bounded fresh retries through `auren_max_iterations` in the execution context, defaulting to three supervisor iterations. Each iteration has execution evidence; repeated failure should terminate rather than loop forever.

`portable.engineering_recovery.EngineeringRecoveryLedger` is wired into the default `AdaptiveRuntime.run()` path. It records each attempt and evidence, caps each same-hypothesis plan at two attempts, persists execution checkpoints, switches the exposed recovery mode to `alternate-plan`, and enforces a total attempt/time budget. `auren_max_iterations` is validated and capped at 12. The exact-head CI helper remains an explicit gate for engineering/release workflows; a general graph run cannot claim CI passed unless its caller supplies and verifies that evidence. The integration tests must remain green before [issue #377](https://github.com/sourabhJainR/AUREN/issues/377) can close.

## Service behavior

The existing service now resumes pending/running durable execution tasks before processing adaptive-learning maintenance. It remains under the platform's existing restart-on-failure service policy.

## Repository-level merge protection boundary

The runtime gate protects handlers registered with an operator-owned gate policy. It cannot intercept a merge performed directly through GitHub UI/API or an alternate automation identity. Repository owners/admins must configure and verify branch protection or rulesets that require the exact-head checks and appropriate review. Until those settings are independently confirmed, report this as an external blocker rather than claiming every merge path is protected.


## Mandatory-gate rollout and rollback

The registration guard intentionally rejects unknown and previously unclassified handlers. Roll out by inventorying each registered handler, reviewing its side effects, registering trusted context and exact-head CI evidence providers first, and then registering consequential handlers with both gate policies. Preserve a database backup and the last-known-good application artifact before rollout. If a deployment is blocked, do not relabel or rename the handler to bypass the guard; inspect the provider configuration and exact-head evidence.

**Rollback:** drain and stop workers, revoke privileged merge/deploy credentials from worker identities, preserve the evidence and execution databases, and restore the last-known-good application artifact only in a non-privileged/read-only mode until it supports the same gate contract. A code revert alone weakens this guard and does not undo external side effects; never re-enable privileged credentials on a reverted controller without independently enforced branch/environment protection. Re-run the negative bypass suite before restoring normal service. Repository branch protection remains a separate owner/admin setting.


## Issue #411: operator acceptance checklist

Do not mark the consequential-promotion gate complete from repository CI alone. The runtime decision gate is only one layer; the repository must also prevent alternate direct merge paths, and the deployed rollback path must be exercised.

### 1. Configure the default-branch ruleset

Open **Repository Settings → Rules → Rulesets → `mainBranchRule`** and configure the active ruleset for the default branch to require:

- Pull requests before merging.
- At least one approval from an authorized reviewer; dismiss stale approvals when the candidate changes.
- Required status checks on the exact candidate commit: `Architecture Integrity`, `AI Harness Check`, `Production AI Harness Hardening`, `Runtime Resilience Matrix`, and `AUREN Portable Bundle`. Select the exact check names shown by GitHub for the current workflows.
- No bypass actors for routine promotion; keep deletion and non-fast-forward protections enabled.
- If the repository uses a code-owner policy, require the designated owner review for the protected paths.

After saving, retrieve the ruleset through GitHub's ruleset API and retain the returned JSON with the acceptance evidence. Verify that the rules include pull-request and required-status-check requirements, not only deletion/non-fast-forward rules. The current audited ruleset `mainBranchRule` (ID `24066603`) has only those latter two rules; it is **not sufficient** to prevent direct pushes.

### 2. Prove direct-write rejection safely

Do not test this by pushing a speculative commit to `main` while the ruleset is unverified: a missing rule could allow the write. Use a disposable repository or a protected test branch with equivalent rules and a normal writer identity (not an administrator/bypass actor). Attempt a direct ref update and retain the GitHub rejection response. Separately verify that a PR with a missing required check and a PR with stale approval cannot merge. Re-run after any ruleset edit.

### 3. Run the deployed rollback rehearsal

Use a non-production deployment with the same handler inventory, credential scopes, evidence providers, persistence backend, and deployment mechanism as production.

1. Record the candidate SHA and a distinct last-known-good SHA. Back up the execution and evidence databases and identify the restore artifact before starting.
2. Attempt promotion with a missing or stale decision record and with a failed/skipped exact-head check. Confirm the consequential handler is not invoked and no external side effect occurs.
3. Run a controlled promotion/canary, trigger the documented rollback condition, and verify the operator's rollback action restores the recorded last-known-good artifact. Confirm a stale worker cannot overwrite the final task outcome and repeat delivery does not duplicate external effects.
4. Retain the signed operator receipt with candidate SHA, last-known-good SHA, trigger, action, observed outcome, operator identity, timestamp, and evidence digests. Export it to the independently controlled evidence sink and verify the retained receipt, not merely the local outbox entry.

### 4. Required closure bundle

Attach or link all of the following to [issue #411](https://github.com/sourabhJainR/AUREN/issues/411):

- The effective ruleset JSON and timestamp.
- The negative direct-write rejection plus stale/missing-check merge rejection evidence.
- The reviewed consequential-handler inventory and trusted provider configuration version/digest (never credentials).
- Exact-head workflow results for the tested deployment.
- The signed rollback rehearsal receipt and verified external-retention receipt.
- Confirmation that the negative bypass tests still pass after rollback.

Until this bundle exists, keep #411 open and treat repository-level promotion as partially guarded. Do not proceed to claim external Arena acceptance on the strength of these local controls.


## Supervisor-owned promotion evidence checks

A consequential handler may declare an optional operator-owned `evidence_checks` array inside its `promotion_gate_policy`. Each entry contains a unique `name`, a non-empty `argv` array, and an optional positive `timeout_seconds` (default 120 seconds). For example:

```python
promotion_gate_policy={
    "context_handler": "trusted-promotion-context",
    "evidence_checks": [
        {
            "name": "candidate-regression",
            "argv": ["python", "-m", "unittest", "tests.test_release_contract"],
            "timeout_seconds": 180,
        },
    ],
}
```

The supervisor persists this policy at trusted handler registration and executes these checks against the provider's live `expected_head` using the bounded evidence gate. It records the result and check lineage in the supervisor-owned evidence graph, then validates that persisted receipt before any consequential handler side effect. Commands use argv with `shell=False`; this is not a sandbox, so operators must still restrict commands and credentials through the host execution environment. A changed checkout, failed command, timeout, missing graph lineage, or invalid receipt blocks promotion. If `evidence_checks` is omitted, the backwards-compatible provider contract still requires the trusted context provider to return `evidence_receipt_node_id` for a complete receipt already persisted in that same supervisor-owned graph.


### Promotion receipt replay binding

When the supervisor runs configured `evidence_checks`, the durable run identity is derived from the task ID, attempt number, and a SHA-256 digest of the canonicalized task payload. Receipt validation requires a matching persisted `evidence_gate_run` lineage node for that exact invocation as well as the live candidate revision. A receipt produced for a different task, attempt, or operation payload is rejected before handler execution. This is replay resistance within the supervisor's durable task boundary, not a substitute for downstream idempotency: consequential handlers must still pass task-scoped idempotency keys to external APIs and reconcile ambiguous outcomes rather than blindly retrying.
