# Evidence-Guided Capability Composition

This phase turns externally evidenced capability gaps into bounded compositions
of existing capabilities.

The planner:
- preserves the gap's evidence lineage;
- searches combinations of at most three capabilities;
- bounds the proposal budget;
- carries novel-domain and failure-condition holdout dimensions;
- binds each proposal to a deterministic digest;
- remains proposal-only.

It does not execute a composition, modify a capability registry, answer an
oracle, or promote lifecycle state. Validation continues through independent
fresh holdouts and the existing invention/counterfactual gates.
