# Resource-Aware Curriculum Scheduler

Phase 20 adds a bounded scheduling policy between curriculum evolution and
execution. Historical resource observations select a local or cloud lane;
duration, memory, and parallelism remain explicit limits.

The scheduler is policy-only. It does not execute work, mutate capabilities,
own an oracle, or bypass the external-evaluation trust boundary.
