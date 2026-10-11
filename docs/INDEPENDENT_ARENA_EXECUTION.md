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
