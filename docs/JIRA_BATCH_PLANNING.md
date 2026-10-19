# Dependency-aware multi-Jira batch planning

`portable.jira_batch_planner.JiraBatchPlanner` derives a safe next slice from an explicitly supplied Jira batch. It does not own Jira records, execution checkpoints, or durable task status; those remain with the Jira connector and AUREN Engineering State Ledger.

## Example

```python
from portable.jira_batch_planner import JiraBatchPlanner, JiraWorkItem

planner = JiraBatchPlanner([
    JiraWorkItem(
        key="APP-123",
        acceptance_criteria=("Provider retry is idempotent", "Recovery test passes"),
        likely_paths=("portable/execution_controller.py", "tests/test_recovery.py"),
    ),
    JiraWorkItem(
        key="APP-124",
        acceptance_criteria=("Dashboard displays durable progress"),
        depends_on=("APP-123",),
        likely_paths=("dashboard/progress.py",),
    ),
    JiraWorkItem(
        key="APP-125",
        acceptance_criteria=("Documentation explains rollback"),
        likely_paths=("docs/rollback.md",),
    ),
])
wave = planner.next_wave(completed=())
print(wave.parallel_groups)
print(wave.blocked)
print(wave.shared_path_conflicts)
```

## Scheduling rules

- Jira keys are normalized and unique; every item must have explicit acceptance criteria.
- Missing dependencies, self-dependencies, cycles, and completion keys outside the batch fail closed.
- Only items whose dependencies are completed are eligible for the next wave.
- Items with overlapping estimated paths are reported as shared-file conflicts and are not placed in the same parallel group.
- Items with no path estimate are isolated until repository impact analysis can identify their likely write set. Unknown paths are not evidence of independence.
- Parallel groups are advisory scheduling hints, not a replacement for branch isolation, locks, exact-head checks, or final diff review.
- Risk tier is explicit. High/critical work still requires the repository's approval and verification gates.

## Integration boundary

The Jira adapter should supply issue keys, acceptance criteria, dependencies, and links to evidence. Repository intelligence should refine likely paths and impact. The canonical Engineering State Ledger owns phase, attempts, checkpoints, approvals, evidence references, and completion. The planner is intentionally pure and disposable so it cannot drift into a competing task database.
