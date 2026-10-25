# Adaptive User Workflow Policy

**Status (2026-10-25):** versioned profile schema and unit tests are implemented on the workflow-profile branch. Execution-controller, CLI/dashboard, repository-action, durable-receipt, and restart integration remain required.
**Applies to:** task intake, planning, repository changes, verification, review, and delivery.

## Goal

AUREN should adapt its working style to the user's explicit preferences while preserving one consistent safety and evidence model. Workflow preferences control how work is presented and staged; they must not silently grant authority, weaken verification, or bypass required repository policy.

## User-controlled workflow profile

A profile may specify:

- **Change delivery:** commit each coherent change, stage changes without committing, keep changes in a working tree, or prepare a patch/PR.
- **Execution cadence:** step-by-step checkpoints, bounded batches, or autonomous continuation until a defined stopping condition.
- **Approval boundary:** ask before edits, ask before commits, ask before push/PR, or permit explicitly scoped actions without per-step confirmation.
- **Review depth:** targeted tests, broad regression, security-focused review, or all applicable checks.
- **Communication:** concise progress, detailed reasoning summary, or final report only.
- **Recovery preference:** retain work-in-progress, create checkpoints, or revert the current task's changes on failure where safe.

Defaults are conservative: inspect first, make scoped changes, preserve user work, run impact-based tests, and request approval for externally visible or irreversible actions unless authority was explicitly granted.

## Resolution and precedence

Resolve preferences in this order:

1. Current explicit user instruction for this task.
2. Explicit project/repository workflow policy.
3. User's saved workflow profile, if enabled and current.
4. Safe defaults.

When two instructions conflict, report the conflict and choose the safer reversible path. Do not infer permission to commit, push, merge, publish, delete, or alter external state from a preference for autonomous execution alone.

## Required workflow behavior

- **Commit mode:** create small, coherent commits only after checks selected by the profile and repository policy pass. Never include unrelated or untracked user files without consent.
- **Stage-only mode:** stage only task-owned paths; do not commit. Report staged and unstaged changes separately.
- **Working-tree mode:** leave changes uncommitted and identify changed paths and validation status.
- **Patch/PR mode:** use a branch and reviewable PR; bind evidence to the exact head SHA.
- **Autonomous mode:** continue through bounded implementation, tests, and safe local fixes until the task is complete or a hard blocker, budget/deadline, approval boundary, or no-progress threshold is reached. Poll existing CI where supported; never claim a check passed while queued, running, skipped, or missing.
- **Failure behavior:** preserve diagnostic evidence, state what is blocked, record the next safe action, and use a documented rollback or recovery path. Do not silently discard work.
- **User data:** protect unrelated changes, configuration, credentials, local memory, and project data during install, upgrade, migration, rollback, and cleanup.

## Safety invariants

Preferences may change workflow presentation and the scope of permitted actions, but cannot override:

- repository branch protection and required review/checks;
- least privilege, secrets handling, extension isolation, or approval requirements;
- exact-head verification and post-merge verification;
- durable receipts, provenance, and truthful states such as blocked, failed, cancelled, timed out, unknown, and unscored;
- rollback, backup, and user-data preservation requirements;
- the rule that uncertain consequential external effects must be reconciled before retry.

No profile or inferred behavior may widen authority beyond the explicit task scope. Any persistent profile must be inspectable, editable, resettable, and versioned. Record the profile version/digest with the run receipt so that results can be reproduced.

## Runtime integration work

This contract is not proof of a fully implemented preference engine. Runtime work must:

1. Define a versioned `WorkflowProfile` schema and validate unknown fields, invalid combinations, and unsafe values.
2. Resolve the effective profile at task start and persist its version/digest with intent, approvals, budgets, acceptance criteria, and rollback plan.
3. Route commit/stage/working-tree/PR behavior through the existing execution controller and repository adapters; do not add a parallel authority system.
4. Apply preference-aware progress detail and checkpoint cadence without changing canonical run state.
5. Add deterministic tests for precedence, profile changes mid-run, conflicting instructions, stage-only behavior, dirty worktrees, untracked files, commit failure, interrupted execution, and recovery.
6. Test that autonomy cannot bypass required checks, reviews, approval gates, or external-effect reconciliation.
7. Expose the effective mode and next action in CLI/dashboard before consequential actions and in the durable receipt after execution.

## Acceptance criteria

- Given each supported workflow mode, AUREN performs only the allowed repository actions and accurately reports staged, committed, pushed, reviewed, and merged states.
- Unrelated user changes survive success, failure, cancellation, restart, upgrade, and rollback.
- Explicit current-task preferences override saved defaults; project security rules and platform-enforced protections remain binding.
- A restart resumes with the same persisted effective profile or blocks with a clear incompatibility reason.
- No commit, push, merge, release, or external side effect is reported successful without authoritative evidence.
- The test suite covers safe and adversarial preference combinations and records the exact tested revision.

## Relationship to readiness gates

This policy does not close the independent Arena campaign (#404), production extension isolation, staging migration/rollback, or final release exit gate. Those require their own evidence and independent/external verification.


### Persistence implementation follow-up — 2026-10-25

The standalone persistence layer is implemented in `portable/workflow_profile_store.py`. It uses canonical JSON, atomic replacement, restrictive POSIX permissions, and fail-closed handling for malformed, incompatible, duplicate-key, oversized, and symlink-backed profiles. Tests are in `tests/test_workflow_profile_store.py`.

This is a persistence primitive only. It is not yet connected to CLI/dashboard selection, supervisor task intent, durable receipts, or action execution. Do not treat a saved preference as authorization or as proof that the requested delivery mode was applied.


### Durable execution binding — 2026-10-25

The execution controller now resolves configured task, project and saved profiles; stores the canonical profile and digest in the durable task envelope; and includes profile identity in lifecycle evidence. Resume/claim validates the digest before dispatch. When no profile is configured, legacy task intent remains unchanged to preserve stable work-key compatibility.

This is not yet action-level enforcement. Repository adapters still need to honor commit-per-change, stage-only, working-tree and pull-request modes while preserving unrelated changes. These preferences never bypass approval, review, CI or authority policy.
