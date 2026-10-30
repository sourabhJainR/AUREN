# External Evidence Freshness Contract

External sources have distinct time fields. AUREN must preserve them as separate facts:

- `published_at`: when the publisher says the item was published.
- `observed_at`: when the source or upstream index says it saw the item.
- `available_at`: when the information became available to the system's historical decision context.
- `retrieved_at`: when AUREN fetched the payload.

Do not infer one field from another. In particular, a fresh retrieval does not make an old or undated source fresh, and a missing publication time must remain missing.

## Scoring

Use `EvidenceFreshnessPolicy.external_weight()` for external source observations. It selects the first non-empty freshness anchor in this order:

1. `available_at`
2. `published_at`
3. `observed_at`

`retrieved_at` is accepted for explicit provenance but is never used as the freshness anchor. If all source-time fields are unknown, malformed, or future-dated relative to the decision time, the external freshness weight is zero.

The legacy `weight()` method remains unchanged for compatibility with older internal evidence records. New external-source integrations must use `external_weight()` and persist all original time fields.

## Point-in-time rules

- A historical decision may only use evidence whose `available_at` is no later than the decision timestamp.
- If `available_at` is unknown, use the publisher's `published_at` or source `observed_at` only when the source contract supports that meaning.
- Do not substitute `retrieved_at` for publication or availability time.
- Unknown timestamps remain unknown and reduce evidence coverage; they do not become neutral or positive evidence.
- Keep source URL, publisher, content digest, provider, extraction version, and time semantics alongside the evidence reference.

## Validation

The regression suite covers missing publication time with a valid source-observation time, retrieval-only timestamps, stale availability, future timestamps, and malformed timestamps. These are repository regression tests, not independent external source verification.
