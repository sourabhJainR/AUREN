# Production Evaluator Trust Roots

AUREN's evaluator attestation trust boundary must verify both cryptographic authenticity and the evaluator/oracle/signer separation policy. A self-reported signer name, an unkeyed digest, or a test callback returning `True` is not production attestation.

## Signature contract

The signer produces an Ed25519 signature over the UTF-8 canonical JSON returned by `evaluation_attestation_signing_message(attestation)`. The payload includes schema `auren-independent-evaluation-ed25519-v1`, the digest of all attestation lineage fields, signer ID, and key ID. Encode the 64-byte signature as canonical unpadded base64url using `encode_evaluation_signature`. The evaluator private key remains in the external signer/PKI; AUREN receives only the public key.

## Key registry requirements

Every `EvaluatorKeyWindow` record contains:

- a unique, pinned key ID (for example `evaluator-key-2026q4`);
- an authenticated signer identity;
- a 32-byte Ed25519 public key (or its 64-character hex representation);
- explicit `not_before_epoch` and `not_after_epoch` values;
- a key-level revocation flag.

Multiple key IDs may refer to one signer during planned rotation, but each key must use distinct public-key material. The selected key ID in the attestation must match the signer and be currently valid. The attestation issue/expiry timestamps must fit within the key's validity window. Revoking one key does not revoke a replacement key for the same signer; whole-signer revocation is a separate policy input.

## Production construction

```python
from portable.evaluator_trust import (
    Ed25519EvaluationAttestationVerifier,
    EvaluatorKeyWindow,
    ProductionSeparationAwareAttestor,
)

keyring = Ed25519EvaluationAttestationVerifier(
    keys=operator_managed_key_windows,
    revoked_signers=operator_revoked_signer_ids,
)
attestor = ProductionSeparationAwareAttestor(
    keyring,
    verify_principal=authenticated_identity_provider.verify_principal,
    principal_verifier_id="enterprise-identity-provider",
    principal_verifier_version="oidc-claims-v3",
)
```

The `verify_principal` callable must come from the deployment's authenticated identity boundary (for example, a validated OIDC/SPIFFE identity source), not a protocol stub or test fixture. The ID/version are recorded as pinned deployment policy identifiers; they are not themselves proof of identity. Production construction rejects a generic signature callback and unpinned/test-like principal-verifier identifiers. If `cryptography` is unavailable, Ed25519 production verification fails closed.

## Rotation and incident response

1. Provision the new public key and its validity window through the operator-controlled trust root.
2. Deploy the matching signer key ID and validate a signed test receipt in a non-production campaign.
3. Add the new key ID while the old key is still valid only for the intended overlap window.
4. After cutover, mark the old key revoked and retain its record for audit; do not reuse its key ID.
5. For signer compromise, revoke the whole signer identity, not just one key.
6. Retain the trust-root version, key fingerprints, verifier version, attestation digest, and decision receipt with the campaign evidence.

This implementation verifies the configured cryptographic and identity boundary. It does not create independent evaluator infrastructure, prove custody of the sealed corpus, or replace an external operator's trust-root review and key-rotation rehearsal.
