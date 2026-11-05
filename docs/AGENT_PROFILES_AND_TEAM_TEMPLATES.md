# Agent profiles and reusable team templates

## Purpose

Agent profiles provide versioned, reviewable defaults for common engineering roles. Team templates describe how those profiles collaborate on a bounded workflow.

Profiles are configuration, not authority. The canonical organization registry, trusted assignment resolver, Guard, approval store and `ExecutionController` remain authoritative. A profile cannot grant a role, widen a task scope, add a tool permission, approve its own work, waive a gate or choose an unrestricted workspace.

## Current contract

The versioned profile registry is `config/agent_profiles.v1.json`. It now covers all 10 canonical organization roles:

- `chief_executive@1`, `chief_technology@1`, and `engineering_manager@1` — executive direction, technical governance, assignment and shared-budget management.
- `principal_architect@1`, `backend_implementer@1`, `qa_reviewer@1`, `security_reviewer@1`, and `reliability_engineer@1` — design, implementation, independent QA/security and reliability/recovery work.
- `independent_evaluator@1` — separately controlled evaluation of pinned artifacts and holdout evidence.
- `finance_reporting@1` — cost, budget variance and evidence-backed reporting.

Versioned templates cover `feature_delivery`, `executive_governance`, `reliability_assurance`, `full_company_delivery`, and `independent_evaluation`. The full company template runs CEO → CTO → engineering manager → architect → implementer → reliability, then QA/security, followed by finance reporting. Independent evaluation remains a separate template and trust domain. The evaluator template is intentionally separate: a distinct persona ID is not proof of evaluator independence. The host must place its runner, credentials, holdouts and receipts outside the candidate trust domain.

Each profile pins its canonical role and reporting manager, an action-class subset, capability requirements, provider/tool preferences, workspace mode, memory namespace, budget defaults, deadline and approval policy. The profile digest is SHA-256 over its normalized versioned content.

The loader rejects unknown roles, reporting-hierarchy drift, action-class expansion, invalid budgets/deadlines, shared memory namespaces, duplicate JSON keys, duplicate revisions, unknown template references and dependency cycles.

## Authority and routing rules

- `action_classes` must be a subset of the canonical role's `allowed_action_classes`.
- Provider preferences, required capabilities and tool preferences are routing hints only. They do not create grants or bypass capability routing.
- Workspace policy must inherit the canonical assignment scope. The profile cannot widen path or resource access.
- Memory namespaces are isolated by profile identity. Task/user/workspace/sensitivity checks remain required in the memory layer.
- Approval policy can require additional review but cannot weaken the role, Guard, approval-ledger or release policy.
- Budget defaults are ceilings/defaults for planning. The canonical supervisor and shared budget remain the runtime authority.

## Execution and rollout boundary

Trusted plan dispatch can pin the exact profile ID, revision and digest into durable task intent. The worker guard resolves and checks that binding again before handler execution, including after restart. `ExecutionController.register_team_from_template` resolves the versioned template and profile digests, then persists immutable membership, dependency edges, template identity and one shared budget. Assignment is gated by completed dependencies. For template-registered teams, each member must first receive a host-authorized principal assignment and a host-verified isolated workspace receipt. The assignment binds the canonical role, manager, action class and scope digest. It also proves the selected provider/model satisfies the persona's required capabilities and provider preferences, that all preferred tools are available, and that the profile's memory namespace is isolated. The readiness digest binds those facts to the exact profile revision and provider-readiness evidence digest. The workspace receipt then binds that principal and assignment digest to the exact member, workspace, sandbox, workspace mode, network policy and no-inherited-secrets policy. Workspace and assignment bindings are immutable and unique within a run. A fresh controller process recovers these records. These are host integration contracts: fixture callbacks do not prove that a production provider, model, tool registry, memory isolation layer, OS/container sandbox or identity provider is configured.

### Shared team-run budget

Registering a team also creates one supervisor-owned budget for the task/run. Its token, tool-call and wall-time ceilings are derived by summing the validated member profile defaults once, then applying hard run-wide caps (2,000,000 tokens, 10,000 tool calls and 86,400 wall seconds). These are shared pools, not per-member copies. Concurrent assignment is capped at four active members (or the team size when smaller).

Before an operation starts, its member reserves capacity under a stable operation key. The SQLite write transaction checks the aggregate of settled usage and outstanding reservations, so parallel members cannot spend the same remaining capacity. Repeating the same key and exact reservation is idempotent; conflicting reuse is rejected. Settlement requires a trusted host-configured usage verifier; caller-supplied numbers alone cannot settle a reservation. The verified metrics and canonical receipt digest are stored, and settlement is idempotent for the same receipt. An overrun is retained as evidence and blocks the member. Reservations with unknown outcomes remain held after restart. Capacity is released only when a trusted host-configured verifier accepts a host receipt proving the operation never started; the canonical receipt digest and reconciliation reason are stored with the release record. Without that verifier, reservations remain held.

This is a durable accounting and admission foundation. Provider and tool adapters must still be installed by the trusted composition root with host usage verifiers. The template lifecycle now enforces role dependencies, principal assignment and isolated workspace binding before assignment, but the host must still provide real dispatchers, OS/container isolation and independent reviewers. Rollback: stop invoking the new team-budget APIs and retain the additive budget and operation rows for audit; do not delete or reset charges.

### Provider and tool adapter enforcement

The supervisor exposes `TeamBudgetAdapterEnforcer` in `portable/team_budget_adapters.py`. Pass the host-owned `team_budget_settlement_verifier` and `team_budget_release_verifier` into `AdaptiveRuntime` at construction. Supply `team_budget_enforcer_factory` to `AdaptiveRuntime`; the factory receives the initialized controller and returns the enforcer before graph workers can access the runtime. This keeps enforcer installation inside the trusted composition root. The factory creates the enforcer with two host-owned callbacks:

- `context_resolver` maps the active authenticated assignment principal to the canonical controller task, team run, member ID, stable operation key and bounded reservation amounts. It must ignore caller-supplied identity, budget and operation-key claims.
- `usage_evidence_resolver` returns the provider/tool host receipt for the completed call. The controller's separately configured `team_budget_settlement_verifier` must authenticate that receipt and return exactly the verified token, tool-call and wall-second metrics.

The composition root installs that enforcer for consequential provider dispatch. Pass the same enforcer to each `MCPStdioToolAdapter(team_budget_enforcer=...)` and `ApprovedToolRegistry(..., team_budget_enforcer=...)` for MCP and approved crew-tool calls. The direct `ExecutionController(team_budget_enforcer=...)` constructor argument remains available when the host creates the controller itself. An active authenticated assignment scope with no enforcer is denied before provider/tool dispatch. An adapter explicitly configured with the enforcer also denies calls when the assignment scope is absent, preventing context-loss bypasses. Non-assigned calls retain their existing path. A successful adapter result is not returned to the assigned worker until trusted usage settlement succeeds. A verified overrun blocks the member and denies success. Exceptions, timeouts, missing receipts and verifier failures keep the reservation held; do not refund an unknown operation. Reconcile it through the existing host receipt path.

`ExecutionController.team_delivery_release_readiness(task_id, run_id)` is the end-to-end acceptance check. It requires the supervisor task and every team member to complete, completed QA and security profiles, no unresolved or overrun budget operations, and a fresh host verification of immutable approval evidence with at least two distinct reviewer principals, none of whom is the implementer. It reports readiness only; `release_authorized` remains false because a separate authorized release action is still required. A passing fixture test is not production release or host-isolation evidence.

Rollback: stop resolving the profile registry and retain the last-known-good configuration. Existing durable tasks and role assignments remain unchanged. Do not rewrite historical task evidence when a profile revision is retired.

## Validation

Run:

```bash
python -m unittest tests.test_agent_profiles tests.test_impact_policy
```

The production hardening workflow also runs `tests.test_agent_profiles`. These tests validate repository contracts only; they do not prove a deployed provider, workspace sandbox, memory isolation or independent reviewer.


The live provider/license and explicit release authorization contracts are documented in [TEAM_PROVIDER_AND_RELEASE_GATES.md](TEAM_PROVIDER_AND_RELEASE_GATES.md). The controller readiness method is still a readiness report only; it never authorizes deployment.


### Persona assignment and isolated workspaces

A template run is not dispatchable until each member has both bindings:

1. `bind_team_member_assignment` accepts only a canonical-role/profile-matched assignment receipt. The host verifier authenticates the principal, action class, scope and assignment digest. Principal IDs are unique within a team run.
2. `bind_team_member_workspace` accepts only a receipt matching that assignment and the profile's workspace mode. The host verifier must attest the OS/container boundary, network policy and absence of inherited ambient secrets. The controller rejects assignment before either receipt is persisted.

The supervisor also rejects a dependent member's assignment until every declared prerequisite is completed. `AdaptiveRuntime` exposes `register_team_template` and `dispatch_team_template` as the supported composition-root API. It refuses team formation or dispatch until the trusted plan dispatch root is configured, and it freezes host callbacks once. Under that facade, `ExecutionController.dispatch_team_from_template` runs the registered template in dependency order. Before each dispatch it revalidates the assignment, workspace and provider/license readiness, then passes the profile, exact assignment, workspace and shared-budget enforcer to the host worker spawner. The spawner returns a signed receipt bound to the exact profile, principal, workspace, provider evidence, budget evidence and output digest. The controller verifies and persists that receipt before marking the member complete. A failed or unknown dispatch is not automatically replayed; a running member must be reconciled first. The host spawner must route every provider/tool call through the supplied shared-budget enforcer and execute the worker inside the assigned OS/container workspace.

Use a separate externally controlled evaluator deployment for `independent_evaluation`; never run the evaluator inside the candidate workspace or give it candidate write access. The controller stores receipt digests and non-secret identifiers, not signatures or credentials.

Rollback: disable template-based team formation at the trusted composition root. Keep the additive membership, dependency, assignment and workspace evidence rows for audit. Do not delete historical receipts or reopen terminal member states.
