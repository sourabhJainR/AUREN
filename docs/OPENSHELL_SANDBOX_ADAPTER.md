# OpenShell sandbox adapter

AUREN now exposes an optional host adapter in `portable.openshell_sandbox` for running a trusted command inside an ephemeral NVIDIA OpenShell sandbox. The backend uses the documented `openshell sandbox create --policy ... --upload ... --no-keep -- COMMAND` flow and provider names rather than passing credential values in command arguments.

## Example

```python
from portable.openshell_sandbox import OpenShellExecutionRequest, OpenShellSandboxBackend

request = OpenShellExecutionRequest(
    name_prefix="auren-ci",
    workspace="/path/to/a-curated-staging-workspace",
    policy_file="/path/to/a-reviewed-policy.yaml",
    command=("/bin/sh", "-lc", "cd /workspace && exec python -m pytest"),
    providers=("github-readonly",),
    timeout_seconds=600,
)
result = OpenShellSandboxBackend().execute(request)
evidence = result.as_evidence()
if not result.passed:
    raise RuntimeError(f"sandboxed execution failed: {evidence['exit_code']}")
```

## Safety contract

- OpenShell CLI availability is mandatory. If the CLI is missing or cannot be invoked, the adapter raises and **does not fall back to host execution**.
- A policy file and a curated workspace directory are mandatory. The exact policy bytes are copied to a read-only temporary snapshot before launch; AUREN records that policy digest and a workspace-tree digest. The workspace must not contain symlinks, and any observed workspace mutation during execution fails the result; the caller/operator remains responsible for reviewing the policy's actual filesystem, syscall, and network allowances.
- Provider names may be supplied, but credential values are never accepted by this adapter or placed in command-line arguments.
- Sandbox names are generated per invocation. Commands run with `--no-keep`; on timeout the adapter attempts to delete the sandbox and returns a failed timeout receipt.
- Raw stdout/stderr are returned to the caller but are excluded from `as_evidence()`; the evidence projection includes output digests and byte counts to reduce accidental secret leakage.
- This adapter does not itself authenticate the gateway, sign receipts, prove the sandbox policy is restrictive, guarantee process termination on timeout, or independently audit kernel isolation. Production deployment still needs policy review, OpenShell version pinning, external isolation validation, and a host-owned durable evidence sink.

## Rollout

This is an opt-in backend, not a replacement for the existing host executor. Enable it only for workers that explicitly choose the sandbox backend and have adapter-level conformance tests. Do not infer that all AUREN agents are sandboxed just because this module exists.

Official CLI reference: [NVIDIA/OpenShell](https://github.com/NVIDIA/OpenShell/).
