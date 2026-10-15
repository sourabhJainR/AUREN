# Production adapter and Arena trust boundaries

## Provider registration

The host injects provider clients and credentials into `ProviderBackedCrewAdapter`. Register only pinned provider versions through `ProductionCrewAdapterRegistry`, check required crew IDs, freeze the registry, and bind its configuration digest into the run evidence. Provider credentials remain outside graph state and crew proposal payloads.

## Handoff delivery

`DurableHandoffLedger` atomically persists queue state and rejects tampered checkpoints. Claims are at-least-once across crashes: downstream side effects must use the stable handoff ID as their idempotency key. A crew cannot mark its own handoff complete; completion requires a distinct independent review receipt. The canonical StateGraph checkpoint and retry budget remain authoritative for task execution.

## Arena signatures and custody

`Ed25519TrustRegistry` verifies detached signatures against operator-injected public keys, key validity windows, revocation state, and distinct role identities. Keep private keys, sealed corpus, oracle answers, replay ledger, and trust-root update authority outside the evaluated runtime. Rotate to a new signer ID when rotating a key and retain the exact trust policy used for each campaign.

Signature verification authenticates bytes under a trusted key; it does not establish that the signer is independent or that its assertions are true. Campaign evidence should bind the exact main SHA, evaluator/runtime/configuration digests, corpus manifest, run nonce, isolation attestation, and result receipt.

## State-root rollback

`activate_state_root` switches a pointer only when the selected root matches the approved manifest digest. `rollback_state_root` atomically restores a verified prior root and retains both roots. Operators must stop writers or use deployment-level fencing before switching; this helper cannot fence another process. Run the rehearsal against disposable state before any live migration.

## Verification boundaries

Unit and CI tests establish local code behavior only. A live migration rehearsal, independent evaluator operation, sealed-corpus custody, and independently audited sandbox remain distinct production activities. Do not fabricate their receipts or infer them from test fixtures.
