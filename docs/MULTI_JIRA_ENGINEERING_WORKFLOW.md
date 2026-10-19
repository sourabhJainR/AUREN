# Multi-Jira engineering workflow

AUREN should treat each Jira as an independent, auditable work item while reusing repository knowledge, context, and verified engineering lessons across the same codebase.

## Lifecycle

1. **Intake**: capture Jira key, problem statement, acceptance criteria, constraints, non-goals, risk, dependencies, and exit conditions. Keep Jira status separate from GitHub issue/PR status.
2. **Snapshot once**: inspect the current branch, HEAD SHA, upstream, staged/unstaged paths, repository instructions, and relevant dependency/test graph. Use `portable.multi_jira_workflow.ReadOnceFileCache` for repeated file access in one session; it reuses unchanged file contents and invalidates only changed paths. Repository indexes remain governed by RepositoryIntelligence/CodebaseIndex.
3. **Learn before reading broadly**: recall relevant verified procedural/semantic lessons from `portable.memory_orchestrator.MemoryOrchestrator`, scoped to this project and backed by source/test/commit evidence. Search the symbol/dependency map first; read full files only when necessary.
4. **Branch from feature**: use `BranchAwareWorkspace.start_task_branch(..., feature_branch=..., target_branch="main")`. It fetches only when the server-advertised SHA differs from the local remote-tracking ref, creates a new Jira branch from the feature branch, and merges the latest target branch without rewriting shared history.
5. **Smart merge**: clean fast-forwards/merges may proceed automatically. Conflicts stop with their paths surfaced for explicit resolution. Never reset, force-push, or silently choose one side. Refuse sync/branch creation while the worktree is dirty.
6. **Implement in small slices**: reuse established modules, map Jira acceptance criteria to tests, isolate overlapping work through separate branches/worktrees, and serialize only changes with genuine shared-file/dependency conflicts.
7. **Stage narrowly**: call `stage_paths([...])` with explicit files only. Never use blanket `git add .` or `git add -A`. Machine-local secrets and IDE/runtime setup files are refused; shared templates such as `.env.example` remain eligible. Keep the repository's ignore rules as a second defense.
8. **Verify exact revision**: run focused tests, relevant regression/security/static/integration checks, inspect the diff and staged paths, and require all mandatory CI checks on the exact PR head before merge.
9. **Learn from evidence**: only publish reusable lessons after a verified outcome. Use `record_verified_jira_learning` to route the lesson through the existing canonical memory owner with Jira source, evidence IDs, immutable intent digest, confidence, and explicit approval. Do not create a sidecar Jira-learning database or promote guesses.
10. **Close the loop**: update Jira with the PR, commit/check evidence, rollout/rollback notes, remaining risks, and reusable lesson references. A successful merge does not by itself prove production acceptance.

## Git helper

`portable.multi_jira_workflow.BranchAwareWorkspace` provides branch/status snapshots, remote SHA comparison, conditional fetch, safe target-branch merge, feature-derived task branch creation, and explicit-path staging protection.

Example:

```python
from portable.multi_jira_workflow import BranchAwareWorkspace, ReadOnceFileCache

workspace = BranchAwareWorkspace(".")
cache = ReadOnceFileCache(".")
snapshot = workspace.snapshot()  # one explicit branch/worktree snapshot
# Inspect acceptance criteria and dependency map before full source reads.
result = workspace.start_task_branch(
    "jira/APP-123-implement-acceptance-criteria",
    feature_branch="feature/current-work",
    target_branch="main",
)
source = cache.read_text("portable/existing_module.py")
# Make changes, run tests, review diff, then stage only intentional source/test/docs:
workspace.stage_paths([
    "portable/existing_module.py",
    "tests/test_existing_module.py",
])
```

The branch helper intentionally leaves a conflicted merge unresolved and never stages developer-local setup. The caller must inspect the conflict and use the repository's approved conflict-resolution/review path.

## Read-once and freshness contract

"Read once" means read file contents once while the file remains unchanged in the current process/session—not ignore subsequent edits. The cache checks file metadata on reuse and re-reads a changed file. Invalidate explicitly after operations that may preserve metadata, including checkout/merge/generation. Repository knowledge indexes must match the current commit; stale index evidence triggers refresh or targeted source reads.

## Developer-local files that must not be staged

Examples include `.env` and local variants, `.envrc`, `.venv/`, `venv/`, `node_modules/`, `.idea/`, local VS Code settings/launch/tasks files, `local.settings.json`, `*.user`, `*.suo`, `*.local.*`, and local Compose overrides. Shared examples/templates and normal source-controlled development configuration are allowed. Secrets remain prohibited regardless of file name.

## Parallel Jira policy

- One Jira = one task branch, intent digest, acceptance checklist, test evidence, and PR.
- Parallelize independent modules/tests; declare file ownership for shared hot spots.
- Before a branch starts or resumes, compare server refs and fetch only when needed.
- Rebase is not the default for shared branches; merge latest target with conflict visibility.
- When another Jira changes a dependency, invalidate affected cached reads and re-run impact analysis/tests.
- Share verified lessons through canonical memory/provenance, never unreviewed private notes.
- Stop on exhausted retry/time budgets, unresolved authority decisions, stale verification, or unresolvable conflicts; preserve the last verified commit.

## Current limitations

This helper operates on a local Git checkout with Git installed and an accessible remote. It does not itself call Jira APIs or merge a pull request. Jira transitions, pull-request merges, production rollout, and approvals remain with their authorized connectors and repository gates.
