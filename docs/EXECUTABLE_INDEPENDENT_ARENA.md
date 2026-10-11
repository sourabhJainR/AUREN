# Executable Independent Arena Kernel


`portable.independent_arena` is the first executable kernel for the Independent Generalization Arena. It validates a benchmark manifest, locks it with SHA-256, presents a narrow task-only view to the evaluated policy, records outcomes by domain and split, and emits a content-addressed receipt.

## Minimal use

```python
from pathlib import Path
from portable.independent_arena import NonceLedger, seal_manifest, run_campaign, verify_receipt

# This manifest belongs to the evaluator. Do not place it in the policy's workspace.
manifest = {
    "benchmark_id": "operator-supplied-campaign-v1",
    "evaluator_id": "independent-evaluator",
    "runtime_id": "candidate-runtime",
    "cases": [
        {
            "case_id": "case-001",
            "domain": "unseen-task-family",
            "split": "sealed_holdout",
            "prompt": "Evaluate the task using your normal interface.",
            "oracle": {"expected": "operator-defined"},
        }
    ],
}
sealed = seal_manifest(manifest)
ledger = NonceLedger(Path.home() / ".auren" / "arena" / "nonces.sqlite3")

def candidate(task):
    # Receives only case_id, domain and prompt; no oracle or split is supplied.
    return {"expected": "operator-defined"}

receipt = run_campaign(
    sealed,
    candidate,
    policy_digest="sha256:<digest-of-the-exact-runtime-and-policy>",
    run_nonce="unique-operator-generated-run-id",
    nonce_ledger=ledger,
)
assert verify_receipt(receipt, sealed)
```

The example above is a protocol smoke test, **not** an independent evaluation: its oracle and candidate are illustrative and controlled by the same script. A real campaign must use an independently controlled evaluator and oracle, an independently generated corpus, separately controlled execution principals, a sealed holdout not available to the runtime, and independently retained receipts.

## Protocol guarantees and limits

- Canonical JSON and SHA-256 bind the benchmark manifest, policy identity, run nonce, results and receipt.
- A persistent SQLite nonce ledger atomically claims each run nonce before execution. Reusing a nonce is rejected across ledger instances/restarts when they share the same database path; keep this database evaluator-owned and durable.
- Duplicate case IDs, unknown splits, missing fields, shared evaluator/runtime identity, and manifest tampering fail closed.
- The policy callback receives only `case_id`, `domain`, and `prompt`; expected labels and split metadata are withheld.
- Exceptions become opaque `error` outcomes rather than leaking exception text into the receipt.
- `result_digest` supports deterministic comparison across repeat runs. `receipt_digest` also binds `issued_at`, so receipts from separate runs are expected to differ.
- Exact structured equality is the default oracle. Domain-specific evaluators should be independently authored and versioned rather than silently weakening this default.
- This module is a protocol kernel, **not an OS sandbox or signature system**. A Python callback can inspect its own process and closures; do not treat in-process separation as a security boundary. Production sealed evaluation requires process/service isolation, access control, a trusted external signer, replay/freshness storage, and independently retained artifacts.

## Current acceptance status

The unit tests exercise task-only projection, manifest tampering, receipt tampering, evaluator/runtime separation, policy exceptions, repeatable result digests, and nonce replay rejection across ledger instances. Passing these tests establishes only the local protocol contract. It does not establish external independence, generalization, coding capability, or AGI. The first campaign remains blocked until an independent operator provides and retains the corpus/oracle and signs or otherwise independently attests to the receipt.


## Canonical external-evaluator boundary

Production sealed campaigns use the canonical exchange boundary in `portable.sealed_arena_boundary`, `portable.sealed_arena_exchange`, and `portable.external_evaluator_gateway`. The lower-level `portable.independent_arena` module remains a deterministic local protocol kernel; it is not a second candidate-process runner.

```python
from pathlib import Path
from portable.external_evaluator_gateway import ExternalEvaluatorCommand, ExternalEvaluatorGateway
from portable.sealed_arena_boundary import SealedArenaBoundary, SealedCampaignRequest, SealedCaseEnvelope
from portable.sealed_arena_replay import SealedArenaReplayLedger

request = SealedCampaignRequest(
    campaign_digest="sha256:<campaign-manifest-digest>",
    corpus_digest="sha256:<sealed-corpus-digest>",
    cases=(
        SealedCaseEnvelope(
            case_id="case-001",
            task_digest="sha256:<task-digest>",
            input_digest="sha256:<input-digest>",
            environment_digest="sha256:<environment-digest>",
            holdout=True,
            transfer_dimensions=("unseen-domain",),
        ),
    ),
    run_nonce="unique-operator-generated-nonce",
)
ledger = SealedArenaReplayLedger(Path("/evaluator-private/arena/nonces.sqlite3"))
gateway = ExternalEvaluatorGateway(
    SealedArenaBoundary(verify_external_signature),
    ledger,
)
evidence = gateway.evaluate(
    request,
    ExternalEvaluatorCommand(("/opt/independent-evaluator/run-campaign",), timeout_seconds=120),
)
```

The external evaluator must return a receipt whose campaign digest, corpus digest, case results, and run nonce match the request, and whose signature is verified by an operator-controlled verifier. The persistent ledger claims the nonce atomically before execution; the nonce remains consumed even if the evaluator fails, so retries must use a fresh nonce. Keep the ledger and sealed corpus outside the candidate's filesystem and trust domain. The gateway uses a subprocess boundary, but that alone is not an OS sandbox; use a restricted container/VM or service identity with explicit filesystem and network policy.

## Sealed-campaign runbook

1. An independent operator generates the benchmark and keeps the sealed holdout and oracle outside the candidate environment.
2. Record the corpus, manifest, candidate/runtime, evaluator, and adapter digests; issue a unique run nonce.
3. Run the candidate under a restricted execution identity and the evaluator under a separate identity.
4. Retain the receipt outside the candidate workspace and verify its signature and lineage using an operator-controlled key or attestation service.
5. Repeat with a fresh nonce and compare outcomes/result digests. A reused nonce must be rejected by the persistent ledger.
6. Publish aggregate metrics, per-domain scores, failures, negative/adversarial outcomes, receipt digests, and limitations. Never publish sealed answers or sensitive prompts.
7. Promote a capability only after source-domain regression, unseen-target transfer, causal attribution, and a fresh independent holdout all pass.

Until an external operator runs this protocol and publishes independently retained evidence, AUREN has an internal regression baseline, **not a completed independent campaign or externally validated coding-capability score**.
