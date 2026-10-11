# Promotion Evidence Chain

The promotion evidence chain joins three independent layers without granting
promotion authority:

1. externally attested evaluation campaign
2. tamper-evident arena run receipt
3. causal intervention and fresh-holdout evidence

The builder checks lineage consistency, trust, contamination, fresh holdouts,
and causal eligibility. It emits an evidence-backed decision record only.

This closes a major gap between having separate evaluation components and being
able to show that a capability improvement is supported by one coherent chain
of evidence.

It is still not proof of AGI. The external evidence must remain genuinely
independent, broad, repeated, and resistant to benchmark contamination.


## External sandbox isolation evidence

A promotion chain is incomplete unless it includes a valid, detached-signature-verified isolation attestation from an independent sandbox auditor. The report is bound to the exact campaign digest, run-receipt digest, and immutable image digest; it must be current and report network denial, no host mounts, read-only root filesystem, non-root execution, credential non-exposure, resource limits, and observed termination. The attestation signer must be distinct from the evaluator, oracle, and policy runtime.

The trusted public key must be provisioned out of band. A valid signature authenticates the report but does not prove the auditor's claims or the host's isolation by itself; deployments still need independent audit and host/runtime evidence. This gate records evidence only and does not promote capabilities or mutate lifecycle state.


## Trusted isolation signer configuration

Isolation verification now requires a non-empty allowlist of trusted auditor signer IDs supplied by deployment configuration out of band. A valid signature from an arbitrary self-selected signer is insufficient. Configure the allowlist from a separately controlled trust root and keep the signature verifier's public keys outside the evaluated runtime. Tests use an explicit fixture allowlist; they do not provision production trust.


## Immutable observation ledger

`ArenaEvidenceObservationLedger` records both accepted and rejected evidence-chain outcomes as content-addressed immutable observations. Only accepted records are exposed through `learning_inputs`; rejected records retain their blocking reasons. Re-ingesting the same receipt is rejected as replay, and a different observation for an already-seen receipt is rejected as a conflict. A new evaluation must produce a new receipt rather than overwrite prior evidence.

With a persistent SQLite database path, the ledger stores observations across process restarts and uses a unique receipt key to reject replay/conflicting writes under concurrent ingestion. Omitting the path creates an in-memory ledger for tests only. Production deployments still need filesystem/database access controls, backups, externally provisioned trust roots, and an operational retention policy; the ledger does not authorize policy changes.
