# Governed OpenShell execution

## Purpose

AUREN's action policy and OpenShell backend are separate defenses. This adapter composes them for a single execution path:

1. Build an explicit action request with a run identity, actor, capability, action, target and risk.
2. Ask the host-configured ActionGovernanceGateway for authorization.
3. If denied or approval is missing, do not invoke OpenShell.
4. If allowed, invoke the OpenShellSandboxBackend with an explicit curated workspace, policy file, command, provider names and finite timeout.
5. Return a receipt linking the request fingerprint and policy verdict to the sandbox policy digest, workspace digest, exit status, timeout and output digests/byte counts.

## Example

```python
from portable.action_governance import ActionGovernanceGateway, ActionPolicy, ActionRequest
from portable.governed_sandbox_execution import execute_governed_sandbox
from portable.openshell_sandbox import OpenShellSandboxBackend, OpenShellExecutionRequest

gateway = ActionGovernanceGateway(ActionPolicy(
    allowed_capabilities=frozenset({"repository.write"}),
    protected_targets=(".github/workflows", "main"),
))
action = ActionRequest(
    run_id="coding-run-42",
    action_id="tests-1",
    actor="auren-host",
    capability="repository.write",
    action="specialist.execute",
    target="src/service.py",
    risk="write",
)
sandbox = OpenShellExecutionRequest(
    name_prefix="auren-tests",
    workspace="/absolute/path/to/curated-workspace",
    policy_file="/absolute/path/to/openshell-policy.yaml",
    command=("python", "-m", "pytest"),
    timeout_seconds=300,
)
receipt = execute_governed_sandbox(
    gateway, OpenShellSandboxBackend(), action, sandbox
)
if not receipt.passed:
    raise RuntimeError("governed sandbox execution did not pass")
persist_to_auren_evidence_graph(receipt.as_evidence())
```

The evidence persistence call above is illustrative: callers must connect it to AUREN's canonical durable evidence owner. The adapter returns a receipt but does not persist it automatically.

## Safety contract

- Denied actions never invoke the sandbox backend.
- There is no host-execution fallback when OpenShell is missing or fails.
- A sandbox command exit failure or workspace-integrity failure is not a passing receipt.
- Evidence contains digests and byte counts rather than raw stdout/stderr. The backend may return raw output to its trusted caller; do not blindly publish it.
- OpenShell's policy file, CLI/gateway version, providers, network policy, and curated workspace must be reviewed and pinned by the operator.
- The caller must ensure that the action target and curated workspace represent the same approved scope. The adapter intentionally does not infer this mapping from paths.
- The governance trace currently resides in process memory and is hash chained, not signed or independently custodied. Persist a copy in durable evidence and authenticate external receipts before treating them as attestation.
- This does not replace a separately operated isolation audit, independent Arena campaign, or live migration/rollback rehearsal.

## Rollout and rollback

This adapter is opt-in. Enable it only for execution paths whose request construction, action target, workspace curation, policy, provider access and evidence persistence have tests. Roll back by disabling the adapter at the host integration point and reverting the integration commit; do not fall back to direct host execution when the sandbox fails.
