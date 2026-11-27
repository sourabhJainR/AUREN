# Paired optimizer campaigns

Aggregates measured paired runs with passed task-bound rubric receipts. The paired identity validator requires matching task, source, context, tools, provider/model, context budget, acceptance, rubric, policy and privacy identity. Metrics require measured input/output tokens, latency and monetary cost; missing fields fail closed. Both receipts must be passed and task-matched.

Eligibility is conservative and per trial: every optimized score must meet the quality floor, every trial's score decline must stay within the configured maximum, and policy-violation counts must not increase. A favorable campaign mean cannot hide a severe regression in one trial. Thresholds must be finite and bounded. The report includes mean and descriptive standard error (not confidence intervals).

This helper reports eligibility for further review only. It does not prove causality, evaluator independence, or authorize promotion.
