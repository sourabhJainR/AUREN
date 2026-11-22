# GitHub write boundary

AUREN treats GitHub mutations as approved external effects. The MCP adapter does not launch a GitHub write tool until all of these checks pass:

1. A host-supplied canonical execution plan identifies the exact repository, implementation branch, base SHA, path scope and optional pull-request base branch. It must explicitly require `github_mcp` and `repository.write`, with positive capability evidence for both.
2. The plan contains exactly one tool contract for the configured GitHub tool. The contract contains the live MCP `inputSchema` captured during planning and a SHA-256 digest of the exact argument object. This avoids copying file contents into the plan.
3. The request identifies the same owner/repository and implementation branch. File mutations list every path, and each path passes `RepositoryTarget.authorize_path`.
4. The write includes the expected current head SHA. Branch creation uses the approved base SHA. Commit creation must have exactly the approved SHA as its parent. PR create/update/merge calls must include an exact expected head SHA and base SHA; PR writes also require `pull_request_base_branch` in the canonical plan.
5. The MCP call binding includes the write preflight, schema digest and argument digest. Before `tools/call`, the adapter performs `tools/list`, finds the configured tool, compares the live schema digest with the plan contract and validates the exact arguments against that schema.
6. The authority resolver returns a durable GitHub provider-operation claim and an `ActionRequest` with capability `repository.write`, action and target from `call_binding.github_write_preflight`, risk `external_effect`, an idempotency key, and evidence refs for `github-write:<digest>` and `canonical-plan:<digest>`.
7. The existing `ActionGovernanceGateway` must require and accept a trusted approval. The `ExecutionAuthorityBinding` must allow the exact capability, action and target. The durable side-effect ledger remains the source of truth for retries and reconciliation.

## Vibe Coding host

Pass a validated `CanonicalExecutionPlan` (or its mapping) to `LocalVibeCodingHost.run(..., execution_plan=plan)`. The host verifies the repository origin, source checkout branch and base SHA before building the isolated workspace. The plan implementation branch must be the source branch or the plan's declared `pull_request_base_branch`. It also probes configured MCP capabilities and calls `CanonicalExecutionPlan.validate_runtime`; missing or unsupported required capabilities block admission. Digest-only tool contracts are accepted only when the host reports the trusted `github_write_digest_contracts` probe.

For an MCP server whose command does not contain `github`, set `github_server: true` on its `mcp_tools` entry. The host also detects common GitHub MCP server command names. All unknown tools on a declared GitHub server are denied; only the explicit read-only allowlist bypasses write preflight.

Example configuration fragment:

```yaml
mcp_tools:
  github:
    argv: ["npx", "-y", "@modelcontextprotocol/server-github"]
    tool_name: "push_files"
    github_server: true
    requires_authority_binding: true
    external_effects: true
```

The canonical plan must contain a tool contract matching the exact configured tool name:

```python
{
    "name": "push_files",
    "schema": <the reviewed MCP inputSchema>,
    "arguments_digest": <SHA-256 of the exact owner/repo/branch/SHA/files object>,
}
```

The authority resolver can read the host-created contract from `call_binding.github_write_preflight`. It must not infer or weaken the action, target, evidence, idempotency or approval requirements.

## Fail-closed behavior

- Missing plan, unverified origin, stale base SHA, repository/branch mismatch, missing path inventory, out-of-scope path, missing expected SHA, missing tool contract, schema drift, unsupported schema keywords, unknown GitHub tool, missing approval or missing durable provider claim blocks the call.
- A GitHub server that does not implement `tools/list` cannot be used for writes.
- Tools that do not accept an expected SHA or whose schema does not match the reviewed contract are rejected. No pathname-only or best-effort fallback is used.
- PR writes are blocked unless the plan declares a PR base branch and the action binds the exact PR head SHA and base SHA.
- The read-only allowlist is explicit. New GitHub tools must be reviewed before they are added.

## Scope and remaining work

This boundary covers GitHub MCP tools routed through `MCPStdioToolAdapter`. Existing AUREN GitHub CLI wrappers already reject subprocess execution while a canonical execution plan is active. It does not make arbitrary Python network calls or a malicious MCP server safe; those still require OS sandboxing, network egress controls and a trusted server binary. Issue #1436 remains open until every external repository write adapter is covered and independent evidence custody is in place.
