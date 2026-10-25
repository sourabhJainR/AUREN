# Adaptive User Workflow Policy

**Status (2026-10-25):** versioned profiles, durable storage, supervisor binding, delivery-mode preflight, CLI show/set/reset, and profile-bound supervisor event receipts are implemented. Dashboard profile projection and task-profile persistence exist. Remaining gaps: repository-adapter-specific receipts, crash-process restart acceptance coverage across all workflow modes, and broader approval/profile-change fault injection.
**Applies to:** task intake, planning, repository changes, verification, review, and delivery.

## Goal

AUREN should adapt its working style to the user's explicit preferences while preserving one consistent safety and evidence model. Workflow preferences control how work is presented and staged; they must not silently grant authority, weaken verification, or bypass required repository policy.

## CLI

Use `auren workflow-profile show` to inspect the saved profile, source, digest,
and authority boundary. Use `auren workflow-profile set --delivery-mode
pull_request` or the other supported mode options to update selected
preferences. Use `auren workflow-profile reset` to return to safe defaults.
The profile is stored under `.auren/workflow_profile.json`; malformed or
incompatible data blocks rather than silently falling back.

## Organization Guard

Organizations may add restrictions with `.auren/guard_policy.json`. Guard
policy uses deterministic deny-overrides evaluation, optional default-deny
rules, approval requirements, duplicate-key rejection, and a canonical policy
digest. Invalid configured policy blocks guarded task dispatch. An allow rule
cannot override mandatory platform, approval, repository, or exact-head CI
gates. See [Organization Guard Policy](ORGANIZATION_GUARD_POLICY.md).

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

- **Commit mode:** create small, coherent commits only after checks selected by the profile and repository policy pass. Never include unrelated or untracked user files without consent. `commit_each_change` does not authorize push, PR creation, merge, release, publish, or deploy.
- **Stage-only mode:** stage only task-owned paths; do not commit, push, create/open a PR, merge, publish, release, or deploy. A stage-only task must stop after staging and verification, even when execution cadence is autonomous. Report staged and unstaged changes separately. The supervisor must reject consequential or approval-gated handlers before dispatch in this mode.
- **Working-tree mode:** leave changes uncommitted and identify changed paths and validation status. Do not commit, push, create/open a PR, or merge.
- **Patch/PR mode:** use a branch and reviewable PR; bind evidence to the exact head SHA. PR mode does not itself authorize merge, release, publish, or deploy; existing operator-owned approval, exact-head CI, promotion, and rollback gates remain mandatory.
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
8. Enforce delivery-mode restrictions in durable supervisor preflight, not only in CLI/dashboard presentation. Infer action from registered handler identity and typed action fields; block stage-only consequential handlers even when their names are generic. Keep the delivery profile non-authoritative: promotion gates remain independently required.
9. Prevent parallel-PR conflicts with a pre-merge integration guard: verify exact base/head SHAs, fail closed when the branch is behind base, and list overlapping open PR paths across all pages. Serialize shared paths deterministically: the oldest open overlapping PR owns the integration slot; newer PRs wait, then refresh the target and rerun exact-head checks. Missing ordering evidence fails closed. Branch from the latest target; never auto-resolve conflicts by choosing a side.

## Acceptance criteria

- Given each supported workflow mode, AUREN performs only the allowed repository actions and accurately reports staged, committed, pushed, reviewed, and merged states.
- Unrelated user changes survive success, failure, cancellation, restart, upgrade, and rollback.
- Explicit current-task preferences override saved defaults; project security rules and platform-enforced protections remain binding.
- A restart resumes with the same persisted effective profile or blocks with a clear incompatibility reason.
- No commit, push, merge, release, or external side effect is reported successful without authoritative evidence.
- The test suite covers safe and adversarial preference combinations and records the exact tested revision.

## Relationship to readiness gates

This policy does not close the independent Arena campaign (#404), production extension isolation, staging migration/rollback, or final release exit gate. Those require their own evidence and independent/external verification.
