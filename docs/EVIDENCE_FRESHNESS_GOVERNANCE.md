# Evidence Freshness Governance

Phase 26 prevents indefinitely retained decision evidence from steering current
execution without a recency check.

Externally ingested decision observations now receive an observed-at timestamp.
EvidenceDrivenDecisionFabric applies a bounded freshness policy before using
those observations. Stale timestamped evidence is excluded; legacy evidence
without a timestamp remains usable for compatibility but does not claim
freshness.

The policy is injectable and accepts an explicit clock for deterministic tests.
Freshness is an evidence eligibility rule, not a capability or lifecycle gate.

This keeps the learning loop from silently treating old execution conditions,
provider behavior, or resource characteristics as current truth.
