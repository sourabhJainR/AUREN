# AUREN governed action gateway

## Purpose

The action gateway brings a deterministic policy decision point to consequential AI-assisted engineering actions. It follows the governance-first patterns evaluated in AUREN's research watchlist: policy-as-code, mediation at the action boundary, approval escalation, minimal audit traces, and evidence-linked decisions.

## Contract

1. Every proposed action is represented by an `ActionRequest` with a run/action identity, actor, capability, action, target, risk class, optional idempotency key, and evidence references.
2. Capabilities and action verbs are allowlisted. Unknown capabilities and verbs fail closed.
3. Protected targets, release actions, and configured high-impact actions require an approval receipt bound to the exact request fingerprint.
4. Approval is valid only when a host-supplied `approval_verifier` accepts it. If no verifier is configured, approval-required actions remain blocked.
5. Release/merge/deploy actions require evidence references. External effects require idempotency keys when the policy says so.
6. `execute` calls its handler only after an allow verdict. Failures are recorded by exception type only; prompts, source text, raw tool output, secrets, and exception messages are not copied into the trace.
7. A hash-chained trace detects modifications to the in-memory event sequence. It is not a digital signature, durable storage guarantee, identity provider, or proof of independent custody.

## Integrating with AUREN

Use the gateway at the last trusted boundary before a tool, repository mutation, specialist handoff with side effects, external API, deployment, or release. Mutating specialist work must declare explicit `write_paths`; a missing write scope is blocked when the gateway is enabled. Agents may propose actions; they must not be the authority that grants their own permissions. Persist events through AUREN's canonical evidence/journal owner when integrating into a production execution path, and bind records to the candidate commit SHA and external effect receipt.

Example:

```python
from portable.action_governance import ActionGovernanceGateway, ActionPolicy, ActionRequest

gateway = ActionGovernanceGateway(
    ActionPolicy(allowed_capabilities=frozenset({"repository.read", "repository.write"}))
)
request = ActionRequest(
    run_id="run-42", action_id="edit-1", actor="builder",
    capability="repository.write", action="edit", target="src/service.py",
)
result = gateway.execute(request, lambda: apply_bounded_patch())
if not result.executed:
    raise PermissionError(result.verdict.reason)
```

## Important boundary

This module is a provider-neutral policy decision point, not a kernel sandbox. It cannot stop a caller that bypasses it, authenticate approval identities by itself, forcibly terminate an uncooperative process, guarantee exactly-once external effects, or prove that a benchmark was independently operated. Production integration must connect the approval verifier, durable journal, actual process/network/filesystem controls, and external effect receipts. NVIDIA OpenShell or another reviewed isolation backend should be evaluated as a separate defense-in-depth boundary rather than treated as a substitute for policy.

## Validation and rollback

- Run the new focused tests and the repository's required CI workflows on the exact PR head.
- Add adapter-level tests proving all consequential tool paths pass through the gateway before enabling it by default.
- Keep the feature opt-in until adapter coverage and compatibility tests pass.
- If a rollout causes false denials or regressions, disable the adapter feature flag, retain the audit records, and revert the integration commit. Do not weaken default-deny behavior to bypass a failing test.
- Independent Arena evaluation, trusted evaluator/corpus custody, external isolation audit, and operator-run migration/rollback remain external evidence gates.
