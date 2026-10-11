# Executable Independent Arena Kernel


`portable.independent_arena` is the first executable kernel for the Independent Generalization Arena. It validates a benchmark manifest, locks it with SHA-256, presents a narrow task-only view to the evaluated policy, records outcomes by domain and split, and emits a content-addressed receipt.

## Minimal use

```python
from portable.independent_arena import seal_manifest, run_campaign, verify_receipt

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

def candidate(task):
    # Receives only case_id, domain and prompt; no oracle or split is supplied.
    return {"expected": "operator-defined"}

receipt = run_campaign(
    sealed,
    candidate,
    policy_digest="sha256:<digest-of-the-exact-runtime-and-policy>",
    run_nonce="unique-operator-generated-run-id",
)
assert verify_receipt(receipt, sealed)
```

The example above is a protocol smoke test, **not** an independent evaluation: its oracle and candidate are illustrative and controlled by the same script. A real campaign must use an independently controlled evaluator and oracle, an independently generated corpus, separately controlled execution principals, a sealed holdout not available to the runtime, and independently retained receipts.

## Protocol guarantees and limits

- Canonical JSON and SHA-256 bind the benchmark manifest, policy identity, run nonce, results and receipt.
- Duplicate case IDs, unknown splits, missing fields, shared evaluator/runtime identity, and manifest tampering fail closed.
- The policy callback receives only `case_id`, `domain`, and `prompt`; expected labels and split metadata are withheld.
- Exceptions become opaque `error` outcomes rather than leaking exception text into the receipt.
- `result_digest` supports deterministic comparison across repeat runs. `receipt_digest` also binds `issued_at`, so receipts from separate runs are expected to differ.
- Exact structured equality is the default oracle. Domain-specific evaluators should be independently authored and versioned rather than silently weakening this default.
- This module is a protocol kernel, **not an OS sandbox or signature system**. A Python callback can inspect its own process and closures; do not treat in-process separation as a security boundary. Production sealed evaluation requires process/service isolation, access control, a trusted external signer, replay/freshness storage, and independently retained artifacts.

## Current acceptance status

The unit tests exercise task-only projection, manifest tampering, receipt tampering, evaluator/runtime separation, policy exceptions, and repeatable result digests. Passing these tests establishes only the local protocol contract. It does not establish external independence, generalization, coding capability, or AGI. The first campaign remains blocked until an independent operator provides and retains the corpus/oracle and signs or otherwise independently attests to the receipt.
