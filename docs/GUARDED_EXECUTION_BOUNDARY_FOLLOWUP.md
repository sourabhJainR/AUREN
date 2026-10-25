# Guarded execution-boundary follow-up inventory

**Status:** partial implementation; not an isolation attestation.  
**Updated:** 2026-10-25

## Enforcement inventory

| Boundary | Existing route/control | What this change adds | Remaining proof required |
|---|---|---|---|
| Registered task handler | ExecutionController final Guard recheck and durable dispatch receipt (PR #978) | No change to controller boundary in this PR | Prove all production handler registrations are classified and nested actions cannot escape the boundary |
| Direct action gateway | ActionGovernanceGateway.execute; reviewed callsite registry in portable/execution_boundary.py; AST regression in tests/test_execution_boundary.py | Durable policy/version/identity/scope/approval/provider-operation/outcome evidence primitive; legacy unbound adapters now reject policy-classified external effects even when a ledger exists | Wire equivalent enforcement to every real provider/MCP/filesystem/network adapter and prove denial before the actual side effect |
| Provider operation | portable/provider_operation_lifecycle.py, portable/side_effect_ledger.py, gateway preflight | GuardActionEvidenceLedger records pending/unknown outcomes and refuses recovery from a broken chain | Wire the ledger to every real provider adapter and verify remote outcome before terminal success |
| MCP/tool dispatch | `portable/vibe_coding_host.py:MCPStdioToolAdapter`; `ProviderOperationLifecycle` wraps side-effect stages | Consequential MCP capabilities require host-derived context and an authorization bound to the exact configured tool name, executable argv digest, environment-allowlist digest, timeout, resolved arguments digest, request digest, task guard and matching provider-operation claim before `Popen` | The resolver must use host-owned identity/repository context and map the bound tool call to an explicit capability/action; remote outcome lookup/reconciliation and OS isolation remain separate gates |
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


## Consequential MCP stdio launch boundary (2026-10-25)

The local host classifies configured MCP capabilities before building adapters, and `MCPStdioToolAdapter` itself defaults to authority-bound mode. Direct callers must explicitly opt out only for an operator-verified read-only tool; provider-operation status lookup is marked read-only at its dedicated call site. Repository, environment, design, test execution, browser, deployment, preview, release and rollback tools require authority binding by default; a known consequential capability cannot disable that requirement. Other configured MCP tools may opt into the same gate with `external_effects: true`.

`MCPStdioToolAdapter` refuses to launch its child process when the trusted authority dispatcher is absent. The host derives workflow/task identity, project/workspace roots, repository revision, worktree digest and stable budget ID from the active workflow, then passes that context to the operator resolver. The resolver contract must not derive those fields from agent-controlled request data. The action authorization is also bound to a digest of the configured MCP tool name, executable argv, resolved arguments and exact request. A consequential call must match an active `executing` record in the same canonical SQLite ledger; the ledger payload digest is recomputed from provider, operation ID and exact stage intent. The action request binds the provider operation ID, provider idempotency key, request digest and configured-call digest (including environment allowlist and timeout). Each tool call gets a distinct deterministic gateway action ID and ledger key, so multiple MCP tools in one stage cannot collide while sharing the same canonical SQLite store. The adapter exposes a one-shot launch callback so the authorized process can be started at most once. Denial, resolver errors, mismatched identity, mismatched tool configuration, missing active claim, external-effect classification without a provider operation, or missing durable-ledger support leave the process unlaunched.

Product/research/web-search/accessibility/task-planning/independent-review/observability capabilities are read-only by default. Security tools default to authority-bound mode and may set `external_effects: false` only when the operator has verified that the configured tool is read-only. The resolver must be created from trusted host identity and repository state; agent-supplied state is not a trusted identity source. Read-only calls without a provider operation bind their action ID to the exact request digest. Any non-read action requires a durable provider operation ID and idempotency key. The gate does not prove OS/container isolation or remote provider outcome. Deployment/release still require provider-native idempotency, authoritative lookup and evidence-backed reconciliation.

Rollback: if host resolver integration is unavailable or fails, keep consequential MCP tools blocked and use the last-known-good workflow with those capabilities disabled. Do not remove the pre-launch gate or restore unbound MCP dispatch.


## MCP read-only launch working-directory boundary (2026-10-25)

The MCP stdio adapter now requires an explicit trusted workspace root whenever a
caller opts out of authority binding for a declared read-only tool. Before
launch, it rejects missing workspace identity, a missing trusted root, and any
resolved working directory outside that root. Host-built MCP adapters and
provider-operation status lookups pin this root to the host-owned isolated
workspace. Regression tests assert that missing and escaping paths are denied
before `subprocess.Popen`.

This narrows the read-only MCP bypass surface; it does not prove that a remote
tool is actually read-only, authorize its arguments, or provide OS-level
filesystem/network isolation. Read-only classification remains an operator
configuration trust boundary and needs independent provider/tool capability
verification.

Rollback: revert the workspace-root parameter and associated checks only if
they break a documented supported invocation; keep authority binding enabled by
default and do not restore unpinned unbound dispatch.


## Provider dispatch receipt binding gate

A provider dispatch response marked `completed` is rejected unless it binds the
exact AUREN operation ID, idempotency key, provider name, and canonical intent
digest. A remote `provider_operation_id` remains required and is treated as the
provider's identifier, not as AUREN's operation ID. Missing or conflicting
binding moves the durable operation to `reconcile_required`; a subsequent call
must not dispatch again.

This is request/receipt binding, not cryptographic provider attestation.
Interrupted outcomes still require trusted provider lookup and independently
verified evidence. If an adapter does not emit these fields, migrate the adapter
and tests; do not restore acceptance of unbound completed receipts.


## Independent OpenShell isolation acceptance probe

`scripts/verify_openshell_isolation.py` is a real-runtime acceptance probe, not
a mock. It requires an explicit non-empty policy and an installed OpenShell CLI.
On Linux, it first confirms the host can reach a selected network target; inside
the sandbox it probes writes outside the curated workspace, network egress, and
visibility of a host-only process marker through `/proc`. It also verifies
that a host-only sentinel remains unchanged. It refuses to claim success when
the network precondition is unavailable or a probe fails.

Run against the reviewed deployment policy:

```bash
python scripts/verify_openshell_isolation.py --policy /path/to/reviewed-policy.yaml
```

This probe has not been run against a real OpenShell runtime in this change.
Until an authorized environment records a successful run, OS isolation remains
**unproven**. A successful run proves only the tested Linux filesystem, egress,
and PID-namespace probes for the recorded policy digest; it does not prove all
syscalls, IPC, kernel escape resistance, or Windows/macOS isolation.

Rollback: retain fail-closed sandbox launch. Do not replace runtime evidence with
unit tests or application Guard decisions.


## Network boundary coverage and remaining direct clients

The bounded pinned HTTPS helper is now used by the GitHub Actions check poller,
the upstream agency-sync fetcher, and the new denial-before-DNS/socket tests.
The PR integration guard is a bootstrap dependency of the CI conflict gate; it
validates HTTPS URLs against `api.github.com` and refuses cross-host redirects,
but still uses the standard library transport and is **not DNS-pinned**. This
exception is explicit so a transport failure cannot silently disable the
merge-safety gate.

The portable updater's archive download and the optional local-LLM endpoint
still have direct network callsites. Updater metadata requests now use the pinned
HTTPS helper with an explicit GitHub host allowlist. They are not considered covered
by the new helper until they are migrated and tested at their actual boundary.
The local-LLM client needs a separate contract because it supports local HTTP
providers as well as optional embedded inference. OS egress policy remains the
backstop for all subprocesses and SDKs that bypass Python-level clients.

Next migration gates: (1) bounded guarded download with an explicit redirect
host chain for the updater; (2) local-LLM endpoint policy that defaults to
loopback and requires an explicit host scope for remote endpoints; (3) an AST
inventory that fails on new direct network APIs without a reviewed disposition.


## Evidence export network scope

The configured HTTPS evidence sink now uses the pinned transport with the exact
operator-configured hostname as its host scope. Public endpoints must resolve
only to public addresses. Private or loopback sinks are denied unless the
operator explicitly configures `AUREN_EVIDENCE_SINK_ALLOWED_CIDRS` with the
approved CIDR range(s). Redirects are not followed, and the request body and
response size remain bounded. The bearer token is sent only to the configured
hostname over verified TLS.
