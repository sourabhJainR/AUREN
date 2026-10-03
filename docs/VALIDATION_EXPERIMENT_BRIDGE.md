# Validation Experiment Bridge

This bridge converts independently verified invention-validation evidence into
a fresh control/treatment experiment proposal.

It enforces:
- trustworthy validation evidence only;
- at least two task families;
- a new independent holdout;
- an independent oracle;
- at least three independent replications;
- explicit intervention lineage;
- deterministic proposal identity.

It does not execute experiments and does not mutate or promote capabilities.

Pipeline:

invention -> validation plan -> validation runner -> trustworthy evidence
-> experiment proposal -> independent replications -> adaptive experiment
-> causal promotion gate
