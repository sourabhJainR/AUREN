# Evaluator attestation trust boundary
AUREN binds an immutable rubric receipt to evaluator identity/version and key ID with HMAC-SHA256. Verification requires an external key registry and key-specific authorization. A trusted key must be explicitly bound to one evaluator identity and allowed version set; keys cannot be reassigned by claims inside receipts.

## Deployment requirements
- Signing keys must be stored in evaluator-service secrets storage/KMS, never candidate workspaces or repository artifacts.
- Provision key registry and key-to-evaluator/version bindings outside candidate/executor control.
- Run evaluator service in a separately controlled boundary with distinct credentials, access and write permissions. IDs and this module do not prove independence.
- HMAC verifiers require secret material. For public/distributed verification use managed public-key signing.
- Holdout custody remains a separate deployment gate.

API: sign_receipt(receipt, key_id, signing_key); verify_attestation(receipt, attestation, trusted_keys, key_authorizations), where key_authorizations maps key IDs to (evaluator ID, allowed versions).
