# Independent Evaluation Attestation

This phase makes external evaluation trust verifiable rather than merely
represented by a non-empty attestation string.

An attestation binds:
- campaign digest;
- sealed corpus digest;
- independent oracle digest;
- evaluator version;
- external signer identity;
- signature.

The verifier is injected to avoid a cryptographic/vendor dependency. A runtime
may consume a verified attestation, but cannot create one or downgrade a failed
signature/lineage/contamination check to trusted evidence.

This remains evidence infrastructure, not proof of AGI. Actual generality still
requires independently generated unseen tasks and external empirical results.
