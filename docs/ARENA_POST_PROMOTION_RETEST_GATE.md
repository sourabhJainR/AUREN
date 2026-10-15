# Arena post-promotion retest gate

The `ArenaPromotionGate` is a decision-only gate for the last step of capability promotion. It does not activate policy or execute rollback; Guard remains the only authority for those actions.

A promotion decision requires all of the following:

- The baseline receipt is bound to an accepted observation that is present in the persistent `ArenaEvidenceObservationLedger`.
- A separate post-promotion run produces a new immutable receipt; replaying the baseline receipt is rejected.
- The retest binds to the expected runtime and configuration/manifest digests, uses the same corpus, oracle, and holdout set, and has no contamination.
- Every holdout case passes and is verified. Failed or missing holdout outcomes block promotion and remain visible in the decision reasons.
- A production-trustworthy attestation is bound to the retest run, receipt digest, runtime, campaign, corpus, oracle, and evaluator version and is current at verification time.
- A full last-known-good commit SHA and rollback-plan digest are present.

The decision is immutable and content-addressed. A successful decision is evidence for Guard to consider; it is not itself an authorization to promote. A valid local fixture or a passing CI run does not establish independent operator/evaluator control or real sealed-corpus custody. Those operational facts must be attested by separately controlled systems.
