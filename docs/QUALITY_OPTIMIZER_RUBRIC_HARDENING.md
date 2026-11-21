# Quality optimizer and rubric hardening

The additive trust-boundary helpers live in `portable.quality_optimizer_contract`.
They wrap the existing optimizer and rubric primitives; they do not replace the
canonical provider lifecycle, evidence ledger, evaluator, or Guard.

## Rubric binding and evidence

- Call `verify_plan_rubric_binding` immediately before execution using the
  canonical plan manifest's pinned binding, plan ID/digest, and task IDs.
  Missing, stale, altered, or differently scoped bindings are rejected.
- Use `evaluate_verified_rubric` at the trusted evaluation boundary. The
  evidence resolver must return canonical evidence records with the exact
  evidence ID, run ID, source digest, artifact digest, evidence type, verified
  status, and custodian identity.
- Evidence must match the evaluated run, source and artifact set. A URL or
  caller-provided string is not evidence. The evidence custodian must be
  distinct from both executor and evaluator.
- Executor/evaluator identity comparison normalizes case and whitespace to
  reject simple aliases. This is a local guard, not proof of independent
  identity issuance or deployment isolation.
- Unknown or missing hard gates remain non-passing. Rubric results remain
  descriptive and cannot authorize dispatch, merge, deployment or promotion.

## Measured optimizer receipts

`ProviderUsageReceipt` accepts provider-reported non-negative input, output
and cached token counts, latency, retries and tool calls. It binds the receipt
to task, context, policy and rubric digests plus provider/model version and
request ID. Monetary cost remains unknown unless both input and output costs
and a price revision are supplied. Estimates must not be represented as
provider usage receipts.

`verify_paired_run_identity` requires task, source, context, tool, provider,
model tier, rubric, policy, privacy scope, acceptance and context-budget
identity to match before a baseline/candidate comparison. This prevents
savings from being attributed to runs that changed the experiment.

## Remaining deployment gates

These helpers do not implement a persistent optimization cache, tenant-scoped
cache reads, cache invalidation, pre-dispatch provider availability refresh,
or sealed independent evaluator custody. Those require integration with the
canonical runtime and evidence store. Do not claim production savings or
independent evaluation until provider receipts, replay, cache isolation,
outage/stale-cache tests, and independent holdout evaluation are verified.
