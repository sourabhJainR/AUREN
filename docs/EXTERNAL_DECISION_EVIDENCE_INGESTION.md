# External Decision Evidence Ingestion

Phase 25 closes the loop between independent external evaluation and the
persistent decision fabric.

ExternalDecisionEvidenceIngestor accepts only:

1. a complete PromotionEvidenceChain;
2. a matching, evidence-eligible CampaignOutcome;
3. explicit independent-oracle verification;
4. a non-contaminated decision observation.

It records a capability node, external campaign node, decision-observation
node, and promotion-chain node in PersistentEvidenceGraph, with immutable
lineage edges.

The observation metadata is shaped specifically for EvidenceDrivenDecisionFabric:
task family, capability, provider, tool path, success, quality, duration, cost,
verification, contamination, and oracle independence.

The ingestor never executes tools and never promotes a capability. It converts
external evidence into durable inputs that later execution decisions may use.

## Trust rule

A graph node being present is not enough to steer execution. The decision
fabric additionally requires verified, uncontaminated observations and applies
confidence and conflict rules. This preserves the separation between evidence
collection, decision policy, execution authority, and lifecycle promotion.
