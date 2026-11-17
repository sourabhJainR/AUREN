# Quality-adjusted optimizer

The optimizer extends the existing token optimizer and context broker contracts.
It does not create a second provider router or execution authority.

## Current implementation

- Required context sections can be pinned during selection. Missing required
  sections or a budget too small to carry them fails closed.
- Optional context remains deterministically ranked and bounded.
- Token costs are reported only when input and output prices are supplied.
  Unknown prices remain unknown; they are never reported as free.
- Model-option recommendations require verified availability, minimum task
  tier, required capabilities, privacy classification and sufficient context
  capacity. The result is a recommendation only; the canonical provider
  lifecycle must revalidate and perform dispatch.
- Cache identity is content-addressed and includes context, model, tool,
  policy, repository, rubric and privacy-scope digests. A cache store must use
  this key and enforce tenant isolation and invalidation; key generation alone
  is not a cache implementation.
- Paired-run comparison rejects mismatched task, model-tier or rubric digests
  and reports token, latency, quality and policy deltas.

## Safety rules

- Cost and latency are optimization objectives only after capability, privacy
  and quality constraints are met.
- A provider without verified availability is not a candidate.
- The optimizer cannot lower a hard task tier, remove required context, change
  Guard policy, or dispatch a provider.
- Paired results are descriptive. Promotion requires the existing independent
  evaluator, rubric, holdout and approval gates.
- Character-based token estimates are approximations. Use provider/tokenizer
  usage receipts for measured token and monetary cost claims.

## Remaining acceptance

A production claim still requires provider-specific pricing/usage receipts,
persistent cache integration with verified tenant isolation, outage and stale
cache tests, deterministic replay, paired task campaigns and independent
quality evaluation. Synthetic or local estimates are not production savings.
