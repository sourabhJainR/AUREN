# Pull-request integration and conflict prevention

AUREN uses short-lived task branches, explicit file ownership, and serialized integration for changes that touch the same paths. Git cannot guarantee that conflicts never occur; this process detects likely conflicts early and prevents a known stale or overlapping branch from passing the integration gate.

## Required workflow

1. **Start from current main.** Fetch the latest base immediately before creating a task branch. Record the base SHA in the task journal/PR body.
2. **Partition work by file ownership.** Parallel tasks may run together when their write sets are disjoint. If two tasks need the same file, assign one owner or split the shared change into a small prerequisite PR. Do not have multiple workers edit the same path concurrently.
3. **Run the integration guard early.** The PR guard fails closed when the branch is behind its base, the API cannot prove the changed-file set, or another open PR edits the same path. Resolve by merging the prerequisite first, then rebasing/recreating the dependent PR on current main.
4. **Keep PRs small and current.** Open a draft early, but update it from current main before expensive CI. Do not copy old documentation hunks into a replacement branch when main already contains them.
5. **Serialize shared-file merges.** A shared-file PR must be integrated first; then refresh the next PR and rerun its checks on the exact new head. Never merge based on the predecessor's checks.
6. **Require exact-head evidence.** Required checks must be successful for the current head SHA; pending, skipped-but-required, missing, or stale checks are not passes. Preserve review/branch protection and use a merge queue where repository rules support it.
7. **Verify after merge.** Confirm the merge SHA is on main, inspect post-merge workflow runs, and keep a rollback commit/path. For runtime/security changes, run impact-selected tests plus broad resilience/hardening checks.

## Autonomous parallel execution contract

- Before dispatch, derive each task's expected write set. Parallelize only disjoint sets; otherwise establish a dependency and single writer.
- Re-check the base SHA immediately before every commit and merge.
- If a sibling PR merges while work is running, stop writing the stale branch. Rebuild or rebase from the new main and replay only the intended semantic change, not a stale patch.
- Never auto-resolve semantic conflicts by choosing ours/theirs or by dropping a hunk. Inspect both intents and preserve the current-main contract.
- Retry only after new evidence: a refreshed base, updated diff, and new exact-head CI.
- The guard is a prevention layer, not proof of no conflict. GitHub mergeability, required checks, independent review, and post-merge verification remain mandatory.

## Rollback

Revert the guard workflow/docs/tests if it causes false blocks. A guard rollback must not change product runtime state. A false positive is resolved by serializing the shared-path PRs or splitting ownership, not by bypassing required checks.
