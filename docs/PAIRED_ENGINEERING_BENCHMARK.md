# Paired AUREN versus direct engineering benchmark

The benchmark contract is designed for matched, evidence-backed runs. It does
not execute coding agents and does not claim a measured AUREN advantage until
real run receipts have been collected.

## Protocol

1. Pin a benchmark manifest: task-suite digest, task family, model ID/tier,
   provider version, context budget, acceptance digest and rubric digest.
2. Run the same task against direct coding and AUREN. Preserve failed runs.
3. Use a separate evaluator identity for each run and bind a run-specific rubric
   receipt plus evidence references.
4. Compare only pairs with the same task ID/digest and exact manifest digest.
5. Report paired deltas for elapsed and active user time, interventions, tokens,
   tool calls, retries, policy violations, escaped defects, requirement coverage,
   evidence completeness and rubric quality. Provider cost stays unknown when
   usage/pricing evidence is absent.
6. Keep recovery success and duplicate/stale side effects as explicit safety
   gates. Quality or safety regression blocks promotion regardless of savings.

## Report semantics

The report includes sample size, each pair, mean and median paired deltas,
promotion-eligible pair count and a small-sample warning. It is descriptive
statistics, not a population-level confidence claim. Failed, blocked and
unscored runs cannot be converted into successful pairs.

## Current boundary

This repository module validates supplied receipts and computes summaries.
It does not launch baseline/candidate workloads, attest provider usage, prove
independent evaluator custody, or produce a public benchmark result. Those
require an operator-run campaign with pinned environments and independently
retained evidence.

## Command-line report builder

After collecting real run receipts, prepare a JSON input with the pinned
`manifest` and a list of `pairs`, each containing one `baseline` and one
`candidate` run. Then run:

```bash
python -m scripts.report_paired_engineering_benchmark --input paired-runs.json --output paired-report.json
```

The output file is created exclusively with owner-only permissions; existing
files are not overwritten. Input is bounded to 16 MiB and validated against the
same manifest/pair contract as the Python API.
