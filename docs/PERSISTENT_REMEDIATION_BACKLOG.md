# Persistent Remediation Backlog

Review findings are no longer limited to one process or execution episode.

`PersistentRemediationBacklog` stores finding identity, lifecycle status, evidence,
attempt count, session/episode provenance, task family, capability, curriculum
condition, and optional resource route in the existing `PersistentMemory` SQLite
store.

## Lifecycle

1. A review finding is upserted by stable finding ID.
2. A repair episode marks it `in_progress`.
3. The result marks it `resolved` or leaves it `pending`.
4. Restarting the process reconstructs the same backlog.
5. `reprioritize()` raises older, severe, repeatedly failing, or blocked findings.
6. `resume_ids()` supplies unresolved work to the next episode.
7. `feed_learning()` publishes evidence-backed unresolved constraints to
   `LearningTransfer`.
8. `feed_curriculum()` publishes remediation observations to
   `AutonomousCurriculumDiscovery`.
9. `resource_routes()` exposes optional routing hints to the existing resource
   selection layer. The resource router remains read-only and execution authority
   stays with the existing runtime gates.

## Ownership

The backlog owns remediation lifecycle state. It does not replace canonical
engineering evidence, learning memory, curriculum decisions, or resource history.

This keeps persistence durable while preserving the existing ownership boundaries.
