# Durable worktree identity and recovery

The `.ai-harness/worktree.py` helper stores a versioned, atomically replaced registry before and after each Git worktree side effect.

Task-bound records pin:
- Full base commit SHA
- Task ID
- Intent digest
- Budget digest
- Normalized owned paths
- Worktree path, branch and lifecycle state

A duplicate worker identity is rejected rather than overwriting existing ownership. Registry corruption fails closed. The legacy name-to-record format remains readable and is upgraded on the next write.

## Resume

After a host restart, `resume` requires the same task, intent, budget and owned paths. It verifies that the worktree path remains under the configured root, is registered to the source checkout, and still points at the pinned base SHA. It checks tracked and untracked changes against the declared path scope. A changed binding, stale base or out-of-scope file blocks resume.

## Cleanup and reconciliation

Removing a task-bound worktree requires explicit operator confirmation that the worker has stopped. Dirty worktrees also require the explicit force option. The helper does not infer process termination from a host restart.

`reconcile` reports missing and orphaned managed worktrees. It does not delete or alter them. An operator must inspect unresolved workers before cleanup.

This helper improves durable local worktree ownership. It does not yet replace the in-memory registry in `GraphWorkspaceCoordinator`, and it does not claim deployment-wide or multi-host coordination.
