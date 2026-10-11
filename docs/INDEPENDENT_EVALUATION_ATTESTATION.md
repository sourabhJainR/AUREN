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
  corpus, and oracle lineage. It deliberately does **not** claim independence:
  a valid signature alone does not establish who controlled the evaluator,
  oracle, or signer.
- `SeparationAwareAttestor` requires a trusted signer, distinct principals,
  and an injected `verify_principal(principal, role)` callback that authenticates
  both evaluator and oracle identities. Missing, false, or failing identity
  verification fails closed. The evidence digest binds both principal IDs,
  authentication decisions, trusted signer policy, and separation outcome.
- `FreshnessAwareAttestor` adds freshness and one-time-use replay checks on
  top of the separation-aware result. Its principal-verification callback must
  be configured too; omitting it means the attestation is not trustworthy.

The cryptographic and principal verifiers are deployment-injected. Production
deployments must use independently controlled keys/identity sources and durable
replay storage. A callback that merely trusts caller-provided names does not
provide real authentication; the deployment owns that trust root.

This remains evidence infrastructure, not proof of AGI. Actual generality still
requires independently generated unseen tasks and external empirical results.
