# Independent Generalization Arena

The Independent Generalization Arena is AUREN's independent evaluation boundary. It evaluates behavior against a sealed corpus owned outside the adaptive runtime.

This document defines the target trust contract, not a claim that a complete external campaign is already operational. Repository-level protocol tests do not prove independent evaluation or general intelligence. The executable sealed-evaluation path and first independent campaign are tracked in [issue #368](https://github.com/sourabhJainR/AUREN/issues/368).

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
containers and mount the corpus read-only. The Python convenience API rejects
in-process policy callables by default because a callable in the evaluator
process can inspect or mutate evaluator state. The
`allow_in_process_runners=True` option exists only for trusted, local tests; it
is not a sandbox and must not be enabled for untrusted policies. Production
campaigns should use the isolated subprocess or container runner. Repository
tests only validate the protocol boundary; they are not independent external
evaluation.

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
