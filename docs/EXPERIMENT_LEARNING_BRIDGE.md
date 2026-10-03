# Experiment Learning Bridge

This component closes the evidence-to-curriculum handoff after an adaptive
experiment. It converts an immutable experiment result into a bounded learning
signal containing failed domains and the next probe conditions.

Routing is deliberately conservative:

- sufficient replicated evidence -> downstream causal review;
- observed holdout regression -> rollback recommendation;
- insufficient replication -> new holdout/replication;
- otherwise -> continue with novel or constraint-shifted probes.

It does not execute experiments, mutate capabilities, or promote changes. The
existing curriculum components remain responsible for selecting actual probes.
