# Evidence-to-Learning Intervention Controller

This phase closes the evidence-to-learning gap without granting the runtime
permission to change itself.

## Contract

`CounterfactualAttribution + LongitudinalTransferProfile -> bounded intervention -> fresh independent holdout`.

A learning recommendation is emitted only when:
- the attribution is trustworthy;
- at least three independently verified treatment/control pairs exist;
- transfer evidence is trustworthy and spans at least two novel domains;
- causal quality lift meets the configured minimum (5% by default).

Negative causal lift produces a rollback recommendation. Weak or missing evidence
produces `insufficient-evidence`. Positive evidence produces exactly one bounded
`LearningIntervention` and a `CampaignRetestContract` requiring a new independent
holdout.

## Trust boundary

The controller is proposal-only. It cannot execute interventions, alter a
capability implementation/registry, bypass the oracle, or promote lifecycle state.
The returned digests bind the intervention and evidence lineage for a later
execution boundary.

## Next phase

A separate execution boundary may consume an approved intervention, produce a
rollback receipt, observe the fresh holdout, and feed the result back into the
causal/evidence graph.
