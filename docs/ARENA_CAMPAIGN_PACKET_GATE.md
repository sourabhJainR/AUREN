# External Arena campaign packet gate

The packet gate rejects incomplete reports, duplicate operator identities,
unverified attestations, inconsistent repeated outcomes, reused nonces, missing
negative-control rejection evidence, malformed digests, missing resource
measurements, and empty limitation statements.

## Evidence contract

- Pin the candidate repository commit, runtime digest, evaluator/oracle digests,
  and corpus/holdout digests.
- Authenticate four distinct operator roles: candidate owner, corpus owner,
  evaluator owner, and sandbox auditor. The verifier must establish identities
  using deployment-controlled trust roots; different strings are not proof of
  different people or organizations.
- Run the exact same campaign twice with fresh nonces. Result digests must match;
  receipt digests must differ.
- Record a matched baseline, resource costs, and explicit limitations.
- Preserve evidence that nonce replay, receipt tampering, corpus mismatch,
  invalid signatures, role overlap, missing isolation, and partial receipts are
  rejected.

This gate validates evidence-packet structure and consistency only. It does not
create independent corpus provenance, authenticate real operators by itself,
or prove that an external campaign ran. Keep the external-campaign completion
claim gated on a production trust verifier and signed artifacts from a
separately controlled operator.
