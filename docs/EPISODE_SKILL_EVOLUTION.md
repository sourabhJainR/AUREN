# Episode-driven Skill Evolution

AER now connects the canonical `EngineeringEpisode` to the SkillOpt-style maintenance lane.

## Closed loop

```text
EngineeringEpisode
   |
   +--> verified evidence
   +--> outcome / capability / task family
   +--> failure DO/DON'T rules
   |
   v
candidate SkillEdit(s)
   |
   v
independent replay corpus
   |
   +--> baseline replay
   +--> candidate replay
   +--> held-out gate
   |
   v
STAGED candidate
   |
   v
independent promotion receipt
   |
   v
ACTIVE planning skill
```

### Safety boundaries

- Only `COMPLETED` or `FAILED` episodes enter skill evolution.
- Episode evidence is mandatory.
- Failed episodes persist their verified `dont_rules` into durable `failure-dont` memory.
- The source episode is excluded from the holdout set.
- A holdout case must come from a different task id.
- Candidate skill changes are bounded by the existing `SkillOptimizer` edit budget.
- The candidate is replayed a second time through an independently supplied evaluator before staging.
- Staged skills are not returned by `planning_skill()`.
- Promotion requires a matching replay digest and explicit promotion evidence.
- Only promoted skills are exposed to future planning.
- Rejected candidates remain in the existing SkillOpt rejected-edit buffer and cannot become active.

The replay evaluator is injected because AER must not assume that a model score is an authoritative engineering result. Callers should use the real task verifier, test runner, benchmark, or other independent evidence-producing evaluator.

## Failure learning

A failed episode's `dont_rules` are written as verified `failure-dont` memories. Future proposal generation can use these rules to avoid repeating a known failure direction. This is additive to the existing SkillOpt rejected-edit buffer: one protects against failed engineering patterns, the other protects against ineffective skill mutations.

## Planning contract

`EpisodeSkillEvolution.planning_skill()` returns only the last explicitly promoted skill. A successful optimizer epoch alone never changes planning behavior.

This keeps the architecture:

`Episode -> learn -> replay -> stage -> independent evidence -> promote -> future planning`

rather than:

`Episode -> mutate live planner`.