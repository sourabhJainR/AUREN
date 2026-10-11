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

## Verification layers

- `IndependentEvaluationAttestor` verifies signature and expected campaign,
  corpus, and oracle lineage. It deliberately **does not claim independence**
  because a valid signature alone does not establish who controlled the
  evaluator, oracle, or signer.
- `SeparationAwareAttestor` applies the trusted-signer policy and requires
  evaluator/oracle/signer separation. Its evidence digest binds the principal
  identities, separation policy, and decision so different trust contexts
  cannot share the same evidence identifier.
- `FreshnessAwareAttestor` adds freshness and one-time-use replay checks on
  top of the separation-aware result.

The cryptographic verifier is injected to avoid a hard dependency on a
particular PKI or vendor. Production deployments must supply a verifier backed
by independently controlled keys and durable replay storage.

This remains evidence infrastructure, not proof of AGI. Actual generality still
requires independently generated unseen tasks and external empirical results.
