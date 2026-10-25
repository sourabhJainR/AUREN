# Guarded execution-boundary follow-up inventory

**Status:** partial implementation; not an isolation attestation.  
**Updated:** 2026-10-25

## Enforcement inventory

| Boundary | Existing route/control | What this change adds | Remaining proof required |
|---|---|---|---|
| Registered task handler | ExecutionController final Guard recheck and durable dispatch receipt (PR #978) | No change to controller boundary in this PR | Prove all production handler registrations are classified and nested actions cannot escape the boundary |
| Direct action gateway | ActionGovernanceGateway.execute; reviewed callsite registry in portable/execution_boundary.py; AST regression in tests/test_execution_boundary.py | Durable policy/version/identity/scope/approval/provider-operation/outcome evidence primitive; legacy unbound adapters now reject policy-classified external effects even when a ledger exists | Wire equivalent enforcement to every real provider/MCP/filesystem/network adapter and prove denial before the actual side effect |
| Provider operation | portable/provider_operation_lifecycle.py, portable/side_effect_ledger.py, gateway preflight | GuardActionEvidenceLedger records pending/unknown outcomes and refuses recovery from a broken chain | Wire the ledger to every real provider adapter and verify remote outcome before terminal success |
| MCP/tool dispatch | Provider and registered-handler integration points | Organization Guard supports a network context matcher | Inventory each MCP client/server and nested tool call; ensure the decision is made at the actual call boundary, not just at plan time |
| Subprocess | Existing supervised subprocess and sandbox adapters | Policy primitives can scope the authorized action before an adapter runs | Enumerate every subprocess launch API and prove argv, environment, cwd, credentials, timeout, cancellation, and child process behavior are mediated |
| Filesystem | Existing repository-relative path validation in authority-bound APIs | Resource matching and delegated capability scope can be captured in evidence | Cover direct open/read/write/delete, symlink traversal, path races, temporary files, and file descriptors; application checks alone are not containment |
| Network | Existing provider clients and adapters | Exact/glob network scope, validated IPv4/IPv6 CIDR policy matching against an explicit `network_ip` context, and durable rate-limit primitive | Still wire trusted destination resolution into every adapter; add DNS rebinding controls, socket-level enforcement, and per-adapter denial-before-connect tests. Missing/invalid `network_ip` with a CIDR rule fails closed. |
| Policy lifecycle | portable/organization_guard.py | portable/guard_policy_management.py: inheritance, deny-overrides precedence, IANA timezone schedules, SQLite rate limits, compare-and-swap activation, integrity-checked rollback | Wire activation and rollback into every runtime entrypoint; audit/dry-run UI and IP/CIDR scopes remain open |
| Recovery/evidence | Durable side-effect and engineering evidence ledgers | Hash-chained Guard action events bind identity, delegated scope, policy version/digest, approval, provider operation, and explicit outcome | External signing/trust anchor, trusted storage, independent remote-outcome verification, revocation/restart fault injection |

## Fail-closed contracts

1. A missing or invalid active policy blocks action dispatch.
2. Policy activation uses compare-and-swap against the expected active version. Rollback requires the expected current version and an intact target digest.
3. A configured rate limit without a durable limiter denies the action. SQLite errors deny the action.
4. A missing action-evidence event, corrupted hash chain, or unresolved pending/unknown provider operation is not success. Reconcile the remote outcome before retrying; never blindly replay.
5. Inherited restrictions accumulate. The supported explicit precedence is deny_overrides; unknown precedence, missing parents, cycles, and excessive inheritance depth are rejected.
6. The hash chain detects inconsistent edits but is not a digital signature and does not prove the event writer's identity.

## Isolation claim boundary

AUREN's Python Guard and policy layer are application controls. They do not prove kernel-level containment. Do not claim that they prevent arbitrary filesystem, process, network, or IPC access. That requires independently tested OS/container controls, least-privilege credentials, and negative tests at the host boundary for each supported platform.

## Acceptance gates still required before claiming adapter-bypass closure

- AST/callsite inventory for provider, MCP, subprocess, filesystem, and network operations; every callsite has a reviewed disposition.
- Regression test for each actual adapter that proves denial occurs before its side effect, including revocation between preflight and dispatch.
- Restart tests for pending provider operations, active-policy corruption, policy revocation, rate-limit persistence, and evidence-chain corruption.
- A supported-platform isolation matrix proving filesystem/process/network/IPC restrictions independently of the Python Guard.
- Exact-head CI and authorized review. Passing unit tests alone does not establish external containment or universal mediation.


## Follow-up implementation note (2026-10-25)

The policy evaluator now validates and canonicalizes IPv4/IPv6 CIDR selectors and evaluates them against an explicit `network_ip` context. A CIDR-bearing policy rejects missing or malformed IP context rather than silently treating it as a non-match. This is policy matching only: it does not resolve hostnames, prevent DNS rebinding, inspect sockets, or prove that every network adapter supplies a trusted address. Those adapter and host-boundary acceptance gates remain open.


## Subprocess launch inventory gate (2026-10-25)

A regression tripwire now inventories direct `subprocess.Popen` callsites under
`portable/` and `.ai-harness/` against an explicit reviewed adapter registry.
Adding a new direct Popen callsite or changing its containing function without a disposition fails the test. The registry
records important limitations (for example, a host process is not a security sandbox and
a container launch is only as strong as its runtime policy).

This is deliberately a **discovery gate**, not proof that every callsite within
an already-listed module is authorized, nor that subprocess aliases, `os.exec*`,
shell wrappers, filesystem access, MCP, provider SDKs, network sockets, or IPC
are fully inventoried. Remaining work is to move from path-level to exact
function/callsite dispositions, add denial-before-side-effect tests to every
adapter, and cover those other execution mechanisms. Do not claim adapter-bypass
closure until those gates and host isolation evidence pass.

Rollback: revert the inventory-test commit if it causes unrelated CI instability;
do not remove the existing execution-boundary controls. Any registry update must
include the callsite review and a regression test in the same change.


## Mandatory binding for unbound external-effect paths (2026-10-25)

The gateway now exposes `unbound_external_effect_preflight()`. It uses the gateway's own classification (`risk == "external_effect"` or an action in the configured `external_effect_actions`), emits a denial event, and returns `execution_authority_binding_required`. The governed sandbox, coding-agency bridge, and team orchestrator call this preflight on legacy paths after durable-ledger preflight. A durable ledger is necessary for safe external effects, but it does not replace immutable run/task/actor/scope/revision/budget binding.

Legacy read/write calls remain compatible when the gateway policy does not classify them as external effects. Authority-bound calls continue through `execute_bound_action`.

Rollback: do not restore unbound external-effect dispatch. If this gate causes an operational regression, disable or deny the affected action route while correcting the caller; keep the preflight and denial-before-handler tests in place.
