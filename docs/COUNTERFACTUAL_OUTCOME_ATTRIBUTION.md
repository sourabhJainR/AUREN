# Counterfactual Outcome Attribution

Phase 28 adds a strict paired-outcome attribution artifact for strategy experiments.

Only observations from the same experiment and same case are compared. Both treatment and control must be independently verified, oracle-backed, and uncontaminated. Unpaired evidence is discarded and an empty paired set fails closed.

The artifact reports quality lift and pass-rate deltas plus a deterministic digest. It does not select a winner, mutate routing, promote capabilities, or execute experiments; those decisions remain behind the existing policy and lifecycle gates.
