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

## Current repository evidence snapshot — 2026-10-28

Reviewed against baseline `main` SHA `da9517e4946313215c477694988f1fb5f6bba052`. Its current-main check set was terminal with 22 successes, 3 skipped checks, zero failures, and no pending checks; all six push-triggered workflows completed successfully. The portable bundle and checksum artifacts were downloaded from [workflow run 40624079442](https://github.com/sourabhJainR/AUREN/actions/runs/40624079442) and verified: outer bundle artifact SHA-256 `a374cd0c1eaebcdc9867e02277d7a98279f8bd2ccc505d4f23e5c2897119d2e6`, outer checksum artifact SHA-256 `d11f620576d9e31482a12c4621845423ffe85fcb1b3c758c71c16ee286df917f`, embedded ZIP SHA-256 `f477069e369f09b5d72cd5afc5647fbc76fd82d7cce9719824cfe247d6f2b154`, and 508 manifest entries verified with no size/hash mismatches. The manifest pins this SHA, version `1.0.6`, and `target_repository_mutation=false`.

These results verify repository CI and artifact integrity only. Three checks were skipped and are not passes. Deployment behavior, independently controlled evaluator/oracle identity, sealed holdout custody, production host isolation, and external benchmark campaigns remain **not verified / blocked**. No AGI/SI or broad-generalization claim is supported. The graph recovery and runtime/learning/delivery regressions are internal evidence, not independent Arena results.

## Explicit non-claim

There is no numeric “superintelligence score” in this matrix. AUREN is being made compatible with rigorous evaluation, learning governance, transfer testing, and rollback. Whether it achieves broadly general or superintelligent performance remains an empirical question requiring independent validation.
