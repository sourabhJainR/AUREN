# Decision Fabric additions

The existing typed DecisionFabric now also carries deterministic policy inputs for
three cross-cutting concerns:

- Evidence trust: model claims, tool observations, deterministic results,
  independent verification, independent review, and production observations are
  ordered. A model claim never counts as verification.
- Uncertainty: confidence, evidence gaps, model disagreement, stale evidence and
  failure risk combine into a bounded uncertainty score that selects verification
  depth.
- Resources: task CPU/memory/GPU/isolation requirements, queue pressure and
  historical local success select a local, cloud or isolated execution lane.

This is policy only. It owns no evidence or resource store and never executes a
task. Existing provider, capability, graph, scheduler and evidence authorities
remain canonical.
