# Guarded execution-boundary follow-up inventory

**Status:** partial implementation; not an isolation attestation.  
**Updated:** 2026-10-25

## Enforcement inventory

| Boundary | Existing route/control | What this change adds | Remaining proof required |
|---|---|---|---|
| Registered task handler | ExecutionController final Guard recheck and durable dispatch receipt (PR #978) | No change to controller boundary in this PR | Prove all production handler registrations are classified and nested actions cannot escape the boundary |
| Direct action gateway | ActionGovernanceGateway.execute; reviewed callsite registry in portable/execution_boundary.py; AST regression in tests/test_execution_boundary.py | Durable policy/version/identity/scope/approval/provider-operation/outcome evidence primitive | Convert every legacy optional-authority branch to mandatory bound mode for consequential effects, or prove a separately safe equivalent |
| Provider operation | portable/provider_operation_lifecycle.py, portable/side_effect_ledger.py, gateway preflight | GuardActionEvidenceLedger records pending/unknown outcomes and refuses recovery from a broken chain | Wire the ledger to every real provider adapter and verify remote outcome before terminal success |
| MCP/tool dispatch | Provider and registered-handler integration points | Organization Guard supports a network context matcher | Inventory each MCP client/server and nested tool call; ensure the decision is made at the actual call boundary, not just at plan time |
| Subprocess | Existing supervised subprocess and sandbox adapters | Policy primitives can scope the authorized action before an adapter runs | Enumerate every subprocess launch API and prove argv, environment, cwd, credentials, timeout, cancellation, and child process behavior are mediated |
| Filesystem | Existing repository-relative path validation in authority-bound APIs | Resource matching and delegated capability scope can be captured in evidence | Cover direct open/read/write/delete, symlink traversal, path races, temporary files, and file descriptors; application checks alone are not containment |
| Network | Existing provider clients and adapters | Optional network-scope match field and durable rate-limit primitive | Add trusted host/IP/CIDR resolution, DNS rebinding controls, socket-level enforcement, and per-adapter tests |
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
