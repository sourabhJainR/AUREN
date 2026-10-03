# Graph Agent Evidence Decision Integration

Phase 24 connects the persistent evidence decision fabric to the existing
GraphAgentTeam resource/counterfactual decision point.

The graph agent team still owns execution, while EvidenceDrivenDecisionFabric
is a policy-only input:

- verified, uncontaminated historical evidence may override a local-vs-agent
  counterfactual when confidence is sufficient;
- insufficient evidence preserves the existing counterfactual/resource path;
- evidence cannot grant execution authority or mutate capabilities;
- the selected verification depth is incorporated into the existing
  inference/verification policy;
- the complete evidence decision digest is carried in ResourceDecision for
  traceability.

The integration intentionally does not replace the existing historical
resource router, counterfactual engine, capability selector, or canary system.
It adds a durable evidence path above those mechanisms and lets measured
external evidence influence the existing decision point.

## Safe behavior

Cold start is conservative. Contaminated or unverified observations cannot
steer the route. The executioner remains responsible for actually invoking
tools, and lifecycle/promotion gates remain separate.
