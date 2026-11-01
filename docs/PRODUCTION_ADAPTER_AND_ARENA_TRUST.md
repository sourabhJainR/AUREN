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


## Docker external evaluator gateway wiring

The production gateway accepts an explicitly verified isolation executor through
its `isolated_executor` constructor argument. The Docker adapter is not selected
implicitly and never enables the gateway by itself. A host bootstrap can wire it
only after loading an operator-managed Ed25519 custody trust registry and
verifying the pinned evaluator image policy:

```python
from portable.docker_external_evaluator_executor import DockerExternalEvaluatorExecutor
from portable.external_evaluator_gateway import ExternalEvaluatorGateway

executor = DockerExternalEvaluatorExecutor(
    image="sha256:PINNED_IMAGE_DIGEST",
)
executor.verify_policy()

gateway = ExternalEvaluatorGateway(
    boundary=trusted_sealed_arena_boundary,
    replay_ledger=shared_replay_ledger,
    isolated_executor=executor,
)
```

The host must supply `trusted_sealed_arena_boundary` backed by an independently
controlled custody trust root and a replay ledger whose deployment scope matches
the campaign. The adapter rechecks the Docker isolation probe on every launch.
The gateway enforces request/output limits, deadline, receipt validation and
cleanup status. Missing trust material, a failed probe, Docker outage, or
unconfirmed termination blocks acceptance. This code path proves adapter
compatibility; it does not prove independent operator custody, a sealed holdout,
a separately administered signer, or protection from a compromised host kernel
or Docker daemon. Those remain external acceptance gates for issue #404.
