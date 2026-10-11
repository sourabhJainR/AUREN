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


## Candidate process boundary

For an operator-controlled candidate adapter that accepts one JSON task on stdin and emits one JSON value on stdout, use the subprocess adapter instead of an in-process callback:

```python
import sys
from portable.independent_arena import NonceLedger, SubprocessPolicy, run_campaign, seal_manifest

sealed = seal_manifest(independently_supplied_manifest)
policy = SubprocessPolicy(
    [sys.executable, "-m", "candidate_adapter"],
    timeout_seconds=30,
    max_output_bytes=1_000_000,
)
receipt = run_campaign(
    sealed,
    policy,
    policy_digest="sha256:<digest-of-runtime-and-adapter>",
    run_nonce="unique-operator-generated-run-id",
    nonce_ledger=NonceLedger("/evaluator-private/arena/nonces.sqlite3"),
)
```

The adapter launches a fresh process per case, sends only the task projection, strips the evaluator's general environment, uses a temporary working directory, enforces a timeout, validates JSON output, and converts failures to opaque error outcomes. This narrows accidental in-memory coupling; it is **not** a complete sandbox. The process can still access resources allowed to its OS identity. For a sealed campaign, run it in an externally controlled container/VM or service account with filesystem/network restrictions and keep the holdout manifest, oracle, nonce database, and retained receipt outside that environment.

## Sealed-campaign runbook

1. Independent operator generates the benchmark and keeps the sealed holdout/oracle outside the candidate environment.
2. Record the corpus digest, evaluator identity, candidate/runtime digest, adapter digest, and the operator-generated nonce.
3. Run candidate cases through the subprocess adapter inside the operator's restricted execution boundary.
4. Retain the receipt and manifest digest outside the candidate workspace; independently sign/attest the receipt using an operator-controlled key or attestation service.
5. Repeat the campaign with a fresh nonce and compare `result_digest` for determinism. Reusing a nonce against the same persistent ledger must fail.
6. Publish aggregate metrics, per-domain scores, failures, negative/adversarial results, receipt digests, and limitations. Never publish sealed answers or sensitive prompts.
7. Do not promote a capability from this result alone: require source-domain regression, unseen target-domain transfer, causal attribution, and a fresh independent holdout.

Until the external operator completes these steps, the Arena implementation has only a local regression baseline, not a completed independent campaign.
