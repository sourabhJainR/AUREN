# AUREN Gates C, D and E: Evidence-Driven Evolution

## Scope

This implementation adds dependency-free primitives for the three gates while reusing AUREN's existing observability, datasets, regression loop, runtime and release lifecycle. It does not introduce a second agent runtime and does not make telemetry, worker output or an evaluator's own claims an authority source.

## Gate C — Evidence-driven learning

Signals can be derived from canonical trace outcomes: task/span failures, low evaluation scores, regression failures and latency threshold breaches. They retain stable trace references and avoid copying prompts, inputs, outputs or arbitrary metadata. Signals also carry normalized estimates of severity, recurrence, confidence, impact, cost and security relevance. They are ranked by risk-adjusted value; frequency alone cannot bury a security or trust-boundary concern.

An improvement hypothesis must name the problem, source evidence, proposed change, expected benefit, acceptance criteria, risk, budget, pinned baseline/candidate revisions and rollback plan. State transitions are constrained. Approval requires an independent reviewer and a decision reference. The learning layer proposes experiments; it cannot alter acceptance criteria or approve itself.

Experiment manifests bind runs to source revisions, dataset digest, evaluator revision, seed, metrics and a finite budget. They may refer to a sealed holdout by identifier, but may not embed its payload. Holdout custody and access control remain responsibilities of the trusted evaluation service.

## Gate D — Adaptive quality and risk controls

Evaluator calibration measures accuracy, false-pass rate and false-fail rate against independently adjudicated binary outcomes. It fails closed on too few cases, missing negative cases or a false-pass rate above the configured limit; callers remain responsible for proving the reference labels are independent. The drift detector compares observations with both a fixed release baseline and an adaptive operational baseline. The fixed baseline remains authoritative for release regression: movement in the adaptive baseline cannot erase a decline from the release reference. These are transparent tolerance checks, not a claim of a fully calibrated statistical drift detector. Sequential tests, evaluator calibration, distribution monitoring and root-cause workflows should be added behind the same evidence contract with independently validated thresholds.

Promotion checks fail closed when any required trust gate is missing or false: evaluator independence, holdout isolation, evidence integrity, required checks, rollback proof and approval evidence. Passing these checks only makes a candidate eligible for trusted-controller approval; it never promotes the candidate directly.

## Gate E — Operational journal and recovery proof

The ImprovementJournal stores append-only JSONL events with monotonic sequence numbers, stable event IDs, canonical serialization, a SHA-256 hash chain, duplicate-event idempotency, conflict detection and fsync. Reads validate the entire chain and reject malformed, reordered, duplicated or tampered records.

The journal is not a distributed lock, provider-side idempotency system, or authority database. The supervisor must serialize writes across workers, reconcile ambiguous remote operations before retrying, validate current policy and evidence, and preserve the journal during cancellation and rollback. A hash chain detects modification; it does not prevent a privileged writer from replacing the whole file or establish trusted authorship.

## Tests

The repository's Agency quality workflow uses unittest discovery. Run the focused tests with:

```bash
python -m unittest discover -s tests -p 'test_agency_evolution_gates.py' -v
```

Then run the repository's required checks on the exact pull-request head, including supported Windows checks. No Gate C/D/E rollout should be considered production-enabled until evaluator/holdout isolation, supervisor recovery, required-check enforcement and rollback are demonstrated in the trusted deployment environment.

## Rollback

Revert the Gate C/D/E implementation commit or pull request. The module is additive and has no import-time side effects; existing runtime paths remain unchanged unless a trusted controller explicitly adopts these contracts. Preserve journal files as evidence before any cleanup.
