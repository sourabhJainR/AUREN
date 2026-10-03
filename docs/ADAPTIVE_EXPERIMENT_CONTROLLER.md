# Adaptive Experiment Controller

The adaptive experiment controller turns campaign interventions into bounded,
replicated experiments without becoming a capability promotion authority.

It provides:

- deterministic control/treatment assignment records;
- explicit experiment, intervention, unit, and campaign lineage;
- treatment-leakage detection;
- independent campaign replication and cross-domain replication;
- aggregate treatment and fresh-holdout effects;
- replication consistency and regression detection;
- minimum replication/observation stopping criteria;
- explicit insufficient-evidence outcomes rather than statistical significance claims;
- rollback recommendations when fresh holdouts regress.

The controller requires every admitted replication to have a fresh holdout,
independent oracle evidence, verification, and no contamination. A replication
must use a distinct campaign, and the retest campaign must differ from the
campaign that produced the intervention.

The controller is decision-only: it does not execute interventions, mutate
capabilities, or grant promotion. Downstream causal evidence and lifecycle
authority remain separate.

Flow:

campaign failure
-> intervention + retest contract
-> control/treatment assignment
-> independent campaign replications
-> aggregate effect
-> stop / insufficient evidence / rollback recommendation
-> downstream causal promotion evidence
