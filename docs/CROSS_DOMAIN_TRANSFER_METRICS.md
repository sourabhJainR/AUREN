# Cross-domain transfer metrics

A `CrossDomainCampaign` binds a named capability to a versioned sealed request and assigns every case to one domain with an explicit novelty flag and evaluation mode: `zero_shot`, `few_shot`, or `adapted`.

External outcome receipts use exchange schema v3 and may carry signed per-case metrics:

- predicted score and realized score;
- uncertainty estimate;
- resource cost in milliseconds;
- whether failure recovery succeeded.

These values are included in the receipt digest and validated for type, finiteness, range, and case binding. `run_with_report` requires metrics for every sealed holdout case; it does not infer missing metrics or convert them to zero.

The report separates:

- **Source regression:** holdout results for domains marked non-novel.
- **Target transfer:** holdout results for domains marked novel.
- **Evaluation mode:** zero-shot, few-shot, and adapted performance reported separately.
- **Per-domain metrics:** pass rate, mean uncertainty, mean absolute calibration error, mean resource cost, and recovery rate.

Each report has a content-addressed digest tied to the campaign, capability, request, and signed receipt. Empty mode cohorts are reported with `cases: 0` and null metrics, not as a zero score.

## Explicit limitations

A baseline comparison is not available unless a separately attested baseline receipt is supplied; this implementation does not claim uplift without one. Novelty flags and domain labels are metadata claims and still require independent corpus provenance to establish that a domain/environment was truly unseen. Internal unit tests and a valid receipt are not proof of AGI or superintelligence.
