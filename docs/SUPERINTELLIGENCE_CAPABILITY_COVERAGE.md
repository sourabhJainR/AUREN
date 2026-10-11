# Empirical Capability Coverage Matrix

**Status:** versioned roadmap and test inventory; not a capability certification.

This matrix tracks the empirical work needed before AUREN could support a credible claim of broad generalization. Current CI is primarily an internal engineering regression baseline. It does not establish multimodal grounding, open-ended learning, scientific discovery, or superintelligence.

The machine-readable source is [`benchmarks/capability_coverage_matrix.v1.json`](../benchmarks/capability_coverage_matrix.v1.json). Each track records its owner, dependencies, trusted data source, runtime/evaluator identity, metrics, current baseline, test command, held-out plan, threat model, CI gates, promotion criteria, and external dependencies.

## Reading status

- **`protocol_implemented_campaign_not_run`** — code/tests exist, but the independent campaign has not been run.
- **`protocol_and_ci_present`** — local protocol and CI coverage exist; external task validity and independent evidence remain unproven.
- **`partial_*`** — some runtime or security primitives exist, but the target capability has not been independently measured.
- **`not_implemented_external_suite_required`** — a modality- or domain-specific benchmark must still be implemented and run.

## Promotion rule

No track is promoted based on self-reported capability, code presence, a green internal fixture suite, or a digest without a trusted signature. A consequential promotion must be bound to exact-head CI, verified external evidence, an independent review, a last-known-good anchor, and a tested rollback. If evidence is missing, stale, contaminated, or not independently authenticated, promotion remains blocked.

## Critical external dependencies

AUREN maintainers can implement protocol and regression coverage, but cannot self-certify independent custody. Completion of the external campaign requires an independent operator to retain the sealed corpus/oracle and private signing key outside the candidate trust domain, run the evaluator on separately administered infrastructure, and publish signed aggregate results, baselines, uncertainty, failures, and limitations. Host isolation, organizational independence, and semantic novelty require operational evidence—not just signatures.

## Explicit non-claim

There is no numeric “superintelligence score” in this matrix. AUREN is being made compatible with rigorous evaluation, learning governance, transfer testing, and rollback. Whether it achieves broadly general or superintelligent performance remains an empirical question requiring independent validation.
