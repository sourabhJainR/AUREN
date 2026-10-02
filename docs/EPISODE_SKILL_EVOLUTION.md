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
## Execution-time group adaptation

The execution planner now records a second, narrower learning signal for each
actual execution group. A group is keyed by its observed member set and receives
evidence-quality, useful-evidence, success, cost and latency history.

On a later execution, repeated group telemetry can trigger a bounded runtime
adaptation before the provider prompt is built:

- weak groups can replace a low-contribution member with a stronger observed capability;
- strong groups can add one complementary capability when the resource and context budgets permit;
- the primary selected capability is protected from replacement;
- only available, model-invocable, risk/network/sandbox-safe options are eligible;
- the existing resource and context budgets remain hard limits.

This is execution-set adaptation, not automatic bundle promotion. The selected
bundle and its promotion lifecycle remain separate from the actual group
decision. Every adaptation is exposed in capability_execution_plan and the
pathway telemetry, while the resulting group evidence is persisted independently
for the next run.

The learning loop is therefore:

execute group -> attribute useful evidence -> persist group history ->
replace/add at next execution -> observe -> repeat

A positive bundle result alone is never enough to claim that every member or
group was useful.
## Execution-strategy learning

The runtime records strategy-level outcomes alongside capability and group telemetry. Strategy quality is attributed to the observed execution-group evidence first, with bundle score used only as a cold-path fallback when group telemetry is unavailable. Reuse requires repeated evidence, a confidence floor, and a bounded improvement over the baseline when baseline history exists. Cold-start and ambiguous cases retain the baseline. Strategy learning is advisory; explicit caller choices and safety, resource and verification policy remain authoritative.

## Strategy canary and promotion

Learned strategies now have an explicit rollout state: candidate -> canary -> promoted, with rollback when the promotion evidence gate fails. Candidate strategies do not replace the baseline. Rollout state is persisted as auditable JSONL telemetry and exposed in the execution plan.


## Continuous execution decision fabric

Execution-mode learning now sits above the existing strategy/group/resource signals. Each mode is a bounded policy covering:

- serial vs. parallel execution;
- verification depth;
- retry/escalation posture;
- resource fraction and parallelism cap.

Mode history is keyed to the exact team/task/mode and requires repeated, confident evidence before selection. A learned mode is observed without changing the current graph on discovery; only a later run can consume a learned mode. Unknown, cold-start and ambiguous cases retain the deterministic baseline.

Mode outcomes are attributed from completed agents' observed evidence, duration and cost, then persisted through `LearningSteward`. The selected mode is exposed in the execution result so the decision is auditable and can feed future counterfactual analysis.

The resulting hierarchy is:

`group evidence -> strategy evidence -> execution-mode evidence -> bounded runtime choice`

No learned mode can exceed the caller's configured agent/resource limits or override explicit strategy choices, dependency constraints, isolation requirements, or verification safety policy.
