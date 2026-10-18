# Evidence-gated harness experiments

AUREN's `portable.harness_experiments.HarnessExperimentController` implements a bounded, AUREN-native Propose–Probe–Compare–Compose loop. It has no runtime dependency on Harness² or an external benchmark service.

## Runtime integration

A caller inside `AdaptiveRuntime` supplies the canonical task guard from `context["_auren_runtime_execution_guard"]` and the durable experiment checkpoint callback from `context["auren_harness_experiment_persist"]`:

- `guard` charges each baseline/candidate probe and judge attempt and enforces the task's shared deadline, cancellation and attempt budget.
- `persist` writes experiment stage receipts inside the canonical execution checkpoint while preserving the independent `runtime_guard` snapshot. Do not pass the guard persistence callback as the experiment persistence callback.
- `propose(base_copy, task, depth, seed)` returns declarative JSON-compatible candidate harness mappings. The controller caps total candidates and refinement depth and never mutates the caller's active mapping.
- `probe(candidate_copy, task, manifest, stage)` must delegate to a host adapter that actually enforces an isolated workspace/process/container, restricted egress and no inherited secrets.
- `judge(candidate_copy, probe_receipt, manifest)` must be separate from the candidate executor and return scored test/security/reliability evidence tied to the exact manifest, task and harness digest.
- `adopt(candidate_copy, expected_base_digest)` must atomically activate only the selected candidate and return the exact active digest. `rollback(expected_base_digest)` must restore and attest the original digest.

## Evidence and fail-closed rules

The controller requires an isolation receipt (`auren-isolation-receipt-v1`) and a separate evaluation receipt (`auren-evaluation-receipt-v1`) for the same manifest, task and exact harness digest. Evaluation must be scored and have tests, security and reliability marked passed, with non-empty evidence references. Candidate quality must exceed the baseline by the configured margin without lowering safety/reliability or exceeding configured cost/latency ratios.

Missing, mismatched or unscored evidence never becomes a zero score and never authorizes adoption. Budget exhaustion and timeout return `unscored`; invalid receipts and failed adoption return failure/rejection. A failed adoption receipt or post-adoption checkpoint triggers an attempt to roll back to the exact base digest. If durable checkpoint storage fails, the controller does not report success.

The isolation and evaluator receipt fields are **host attestations**, not proof created by this coordinator. A production adapter must enforce the claims and protect evaluator keys, benchmark rubrics and sealed holdouts outside the candidate's authority. Unit tests using fake adapters validate controller contracts only; they do not prove OS/container isolation, external egress controls or independent evaluation.

## Rollback

Disable the experiment controller at the caller and revert the introducing PR if shared-budget accounting, checkpoint lineage, adoption atomicity or rollback behavior regresses. The active harness is not modified by the controller itself; the host's adoption adapter is the only component allowed to change it.


## Isolation trust boundary

A probe receipt's `isolation_enforced`, `egress_restricted`, and `secrets_absent` fields are claims, not proof. The controller now requires an injected `verify_isolation_receipt` callback and fails closed if no verifier is configured, the verifier returns anything other than literal `True`, or verification raises. The callback must validate the receipt against an out-of-band trusted operator and key allowlist, bind it to the exact experiment manifest/task/harness digest, check freshness and required isolation controls, and cryptographically verify the attestation. A verifier based only on fields returned by the candidate executor is not acceptable.

AUREN already provides `portable.arena_isolation_attestation.verify_arena_isolation_attestation` for signed Arena attestations. A production adapter should bind its verified result to the harness probe receipt and the exact immutable image/run digests; test fixtures use a clearly test-only verifier and do not constitute isolation evidence. Without a configured trusted verifier, the experiment remains blocked/unscored and cannot adopt a candidate.
