# Independent Generalization Arena

The arena is the next evidence boundary for AUREN. It evaluates behavior against a
sealed corpus that is owned outside the adaptive runtime.

## Boundary

- The benchmark root is supplied independently and must be outside the runtime root.
- manifest.json seals benchmark identity, train/holdout domains, and oracle IDs.
- cases.json contains the task corpus; the adaptive runtime does not generate or rewrite it.
- Oracle functions are supplied by the evaluator, not discovered from runtime state.
- Evaluation is fail-closed on split, oracle, identity, execution, or evidence violations.
- The arena has no execution authority and does not promote capabilities.

## Recommended deployment

Keep these components in separate storage and trust boundaries:

1. Adaptive runtime: plans and executes submitted tasks.
2. Sealed corpus: read-only benchmark data controlled by the evaluator.
3. Oracle service/code: independently controlled expected-outcome logic.
4. Arena runner: evaluates results and emits evidence.
5. Holdout store: withheld until a release/evaluation window.

For stronger isolation, run the runtime and oracle in separate processes or
containers and mount the corpus read-only. Repository tests only validate the
protocol boundary; they are not independent external evaluation.

## Required evidence

A credible external run should retain:

- immutable corpus/manifest digest;
- case and domain identity;
- holdout status;
- independent oracle identity;
- runtime execution receipt;
- output/evidence identifier;
- pass/fail and verification outcome;
- resource/time measurements;
- evaluator version.

A passing repository test is not evidence of general intelligence.
