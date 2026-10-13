# Independent Arena execution boundary

The Independent Generalization Arena separates task presentation from oracle scoring and produces content-addressed run evidence.

## Execution paths

- `portable.independent_arena.run_campaign` is the lightweight in-process path for trusted development policies and unit tests.
- `portable.isolated_arena_runner.run_campaign_isolated` launches the evaluated policy as a fresh subprocess for each case. The evaluator retains the sealed manifest and oracle in the parent process. The child receives one task object on stdin with exactly `case_id`, `domain`, and `prompt`; it must emit one JSON value on stdout.
- A persistent `NonceLedger` rejects replay of a run nonce for the same campaign/corpus binding.

## Digest semantics

- `manifest_digest` binds the normalized benchmark and oracle material held by the evaluator.
- `result_digest` binds normalized outcomes, domain scores, runtime/evaluator identifiers, and policy digest. It deliberately excludes the run nonce and issuance time, so independent fresh runs with the same fixed inputs can be compared for deterministic outcomes.
- `receipt_digest` binds the full issued receipt, including run nonce and timestamp. Receipts from separate runs remain distinct even when their result digests match.

A matching result digest is evidence of deterministic normalized outputs for the bound inputs; it is not by itself proof of evaluator independence or cross-domain generalization.

## Container-backed output boundary

`portable.container_sandboxed_arena.run_campaign_container_sandboxed` requires a digest-pinned image, disables networking, mounts no host volumes, uses a read-only root filesystem and an unprivileged UID/GID, and applies CPU, memory, PID, and temporary-filesystem limits. Stdout is streamed with a configurable byte ceiling (65,536 bytes by default). Overflow kills the runtime client, requests forced removal of the named container, and records an error rather than accepting a partial answer.

## Process and trust limits

The subprocess runner does not use a shell, bounds execution time, discards stderr from the receipt path, uses a minimal allowlisted environment, and rejects explicit policy environment variables that look like evaluator secrets. On POSIX, timeout and output-overflow handling kill the policy process group; on Windows, the runner starts a new process group and requests tree termination through `taskkill /T /F`, with direct-child termination as a best-effort fallback. It does **not** provide a kernel/container/network/filesystem sandbox. A policy process may still access resources permitted to its OS identity and may attempt to inspect its surrounding machine.

For untrusted policies and independent campaigns, execute the runner under a separately controlled OS account, container/VM or external sandbox with network and filesystem restrictions. The benchmark owner must retain the sealed corpus and signing keys outside the evaluated runtime's control. Do not treat an in-process callback, a local subprocess, or internal tests as proof of an independent evaluation.

## Failure handling

Malformed input, duplicate cases, digest mismatch, nonce replay, non-zero policy exit, invalid JSON output, and policy timeout fail closed or produce explicit per-case `error` outcomes. A receipt with errors is a valid record of a failed evaluation, not a successful campaign. Promotion remains behind Guard authorization and separate evidence/rollback gates.


## Trusted target-domain custody

Sealed cross-domain runs require an out-of-band allowlist of trusted target-domain operators. Each novel target domain must carry a non-empty signature whose signer ID matches its named independent operator, and the configured verifier must cryptographically validate that signature. Missing trust configuration, unsigned provenance, an untrusted operator, or evaluator/oracle overlap fails closed before the external evaluator is called. Test signatures and allowlists are fixtures only; production trust roots must be provisioned independently.


## Cross-domain measurement contract

`portable.independent_transfer_learning_lifecycle.DomainEvaluationObservation` captures case-level task family, environment, evaluation mode (zero-shot, few-shot, or adapted), outcome, uncertainty, calibration error, resource cost, recovery attempts, source regression, run-receipt digest, and target provenance digest. `DomainEvaluationReport.summarize` reports the three target-domain modes separately and preserves source-domain regression counts rather than hiding regressions inside an aggregate score.

A non-empty provenance digest is only a reference to evidence, not proof that the target is genuinely unseen or independently controlled. Promotion still requires the external attestation, sealed holdout custody, and sandbox-isolation gates described above. No report from internal fixtures is an independent generalization result.


## External campaign runbook (issue #404)

This runbook is an operator handoff, not evidence that a campaign has run. Keep issue #404 open until an independent operator supplies the signed artifacts listed below.

### 1. Establish independent control

- Select a task family and project/environment that were not used to develop or tune the capability under test. Record the rationale and a source-domain overlap check.
- Use an operator, evaluator, oracle, and sandbox auditor with separately authenticated identities. No identity may be reused across prohibited roles.
- Provision signer public keys and trust policy out of band. Keep private signing keys, oracle answers, and sealed holdout material outside AUREN and the evaluated runtime.
- Pin the exact AUREN `main` SHA, evaluator version, runtime image digest, configuration digest, and environment version before the run.

### 2. Seal and execute

- Create a versioned manifest with content-addressed cases, task-family/environment identifiers, train/evaluation/holdout split, corpus digest, and signed custody provenance.
- Execute through an independently administered sandbox. Collect signed isolation evidence for network denial, no host mounts, read-only root, non-root identity, credential isolation, CPU/memory/PID limits, and observed termination.
- Run the same fixed campaign twice with fresh run IDs/nonces. Retain each full receipt and attestation; compare normalized outcome/result digests for deterministic consistency without exposing holdout contents.
- Execute negative controls: nonce replay, receipt/corpus mismatch, invalid or expired signatures, signer overlap, missing isolation controls, contamination, partial receipt coverage, and malformed outcome data. Preserve the rejected evidence and reasons.

### 3. Report and preserve evidence

- Report baseline and capability results separately for zero-shot, few-shot, and adapted modes; include source-domain regressions, target-domain success, uncertainty/calibration, resource cost, recovery attempts, failures, and sample sizes.
- Publish only sanitized manifests/digests, trust-root fingerprints, signed receipts/attestations, verifier versions, repeat-run comparison, baseline method, and limitations. Never publish hidden prompts, expected answers, oracle material, or identifying data that enables holdout reconstruction.
- Have an independent reviewer verify signatures, role separation, custody provenance, sandbox claims, and exact receipt-to-report lineage. A valid signature authenticates a claim; it is not an independent audit of the claim's truth.

### Required artifacts before closing #404

1. Operator/evaluator/oracle/sandbox-auditor identity and role-separation statement.
2. Sealed corpus manifest digest and independently signed custody provenance.
3. Exact runtime, evaluator, configuration, and immutable image digests.
4. At least two fresh signed run receipts and deterministic comparison.
5. Signed isolation attestations and evidence of the controls observed.
6. Baseline comparison plus zero-shot/few-shot/adapted metrics and source regression results.
7. Negative/adversarial test results, rejected records, resource usage, uncertainty, failures, and limitations.
8. Independent reviewer sign-off and a sanitized reproducible report.

Do not mark the campaign complete from unit tests, test signatures, internal fixtures, or a self-reported isolation manifest. If an independent operator or required artifact is unavailable, keep promotion blocked and #404 open.


## Production signer lifecycle

Construct the production boundary with an operator-managed public-key registry and
persistent replay ledger. The production constructor accepts `revoked_signers`
and per-key `key_validity` windows; these controls apply to both the external
outcome signature and the corpus-custody signature. Keep the registry, revocation
list, validity windows, and replay database under evaluator/operator control and
outside the evaluated runtime. Rotate signer IDs when keys rotate, revoke compromised
IDs promptly, and retain the key policy used for each campaign with the external
receipt archive. Unit tests prove local verification behavior only; deployment
must verify that key updates and replay state survive restart and are operated by
a principal separate from the evaluated runtime.
