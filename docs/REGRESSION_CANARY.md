# AUREN Regression Corpus and Shadow/Canary Evaluation

AUREN now treats learned policy changes as experiments before activation:

`observe -> learn -> corpus -> shadow -> canary -> promote -> monitor -> rollback`

## Regression corpus

`.ai-harness/regressions/corpus.jsonl` is provider-neutral JSONL. Each case has a stable `case_id`, `task_class`, expected success/verification outcome, risk, and optional tags.

The corpus is checked in and validated in CI. Production systems may supply a larger external corpus, but the repository corpus remains the deterministic minimum gate.

## Shadow evaluation

Shadow evaluation executes a candidate without changing active policy state. It reports:

- pass rate
- verification rate
- average latency
- average token cost
- failing case IDs

Shadow results are written to the learning audit stream and are not sufficient by themselves to activate a policy.

## Canary evaluation

Canary evaluation runs the candidate against the same deterministic contract with explicit thresholds. The default gate requires 100% pass rate and 100% verification rate over the selected canary set.

A canary failure blocks promotion. Lower thresholds must be an explicit caller choice and should only be used for bounded experimentation.

## Promotion and rollback

A candidate must pass deterministic replay and the canary gate, and still satisfy the learning engine's confidence/risk gate. Promotion creates a versioned active policy and supersedes the previous active policy for the task class.

Post-promotion telemetry is evaluated by `PolicyHealth`. Material acceptance degradation or regression-rate increase triggers rollback, which restores the most recent superseded policy where available.

## Safety boundary

Learned policies can tune retrieval/context strategy, but cannot grant permissions, credentials, production access, merge authority, or security exceptions. Explicit risk and repository controls remain authoritative.
