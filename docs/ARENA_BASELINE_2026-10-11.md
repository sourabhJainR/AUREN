# AUREN Arena Baseline — 2026-10-11

## Status

**Internal regression baseline only — not an independent coding-capability benchmark.**

This report preserves the checked-in evaluation results that are currently reproducible in AUREN CI. The test cases, evaluator, and workflow are repository-controlled, so these measurements must not be presented as independent generalization, external coding capability, or AGI evidence.

## Run provenance

- Repository: `sourabhJainR/AUREN`
- Evaluated commit: `634642f4f72863778a53f9d1fecada02bb0d2562`
- Workflow run: [AI Harness Check #38105681905](https://github.com/sourabhJainR/AUREN/actions/runs/38105681905)
- PR that introduced the parallel CI layout: [#390](https://github.com/sourabhJainR/AUREN/pull/390)
- Arena tracking issue: [#368](https://github.com/sourabhJainR/AUREN/issues/368)

## Results

| Internal suite | Result | Interpretation |
|---|---:|---|
| Deterministic routing evaluations | 20/20 (100%) | All checked-in routing fixtures passed |
| P0 contract evaluations | 8/8 (100%) | All checked-in P0 contracts passed |
| P1 integration evaluations | 8/8 (100%) | All checked-in P1 integration fixtures passed |
| P2 capability evaluations | 8/8 (100%) | All checked-in P2 capability fixtures passed |
| Harness evaluation, convention profile, placement analyzer, worktree helper | Passed | Internal regression checks passed |
| Reference applications | Passed | Habit Tracker and Family Financial Register test jobs passed |

The workflow was also measured at approximately **45 seconds**, compared with a previous approximately **49-second** reference after removal of the duplicate full-suite invocation. These are wall-clock observations from separate hosted runs, not a controlled performance experiment.

## What this does and does not establish

It establishes a reproducible internal regression floor for the listed fixtures. It does **not** establish:
- success on an independently authored coding benchmark;
- performance on genuinely unseen domains or tasks;
- independence of the evaluator or oracle;
- robustness to benchmark leakage, adversarial cases, or evaluator manipulation;
- reproducibility under a separately controlled operator and execution environment.

## Next acceptance gate: independent coding-capability baseline

Do not fill the score table with self-authored tasks and call it independent. An external operator must:

1. Supply a versioned corpus with development, evaluation, and sealed-holdout splits; keep holdout prompts and oracle outside the candidate trust domain.
2. Identify and hash the exact AUREN runtime, candidate adapter, evaluator, corpus, and oracle.
3. Run the candidate under a restricted OS/container identity and run the evaluator under a separate identity.
4. Use the canonical sealed exchange boundary and durable nonce ledger in `portable.sealed_arena_boundary`, `portable.sealed_arena_exchange`, `portable.external_evaluator_gateway`, and `portable.sealed_arena_replay`.
5. Retain and independently verify a signed receipt bound to campaign, corpus, case outcomes, and run nonce.
6. Repeat with a fresh nonce; publish aggregate and per-domain results, failures, negative/adversarial outcomes, resource costs, and receipt digests without exposing sealed answers.
7. Re-test after any capability change on a fresh holdout before promotion.

Until those artifacts are independently retained, the independent campaign and coding-capability baseline remain **not run**. See [the executable Arena protocol](EXECUTABLE_INDEPENDENT_ARENA.md) and [the independent generalization contract](INDEPENDENT_GENERALIZATION_ARENA.md).
