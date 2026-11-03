# Agent profiles and reusable team templates

## Purpose

Agent profiles provide versioned, reviewable defaults for common engineering roles. Team templates describe how those profiles collaborate on a bounded workflow.

Profiles are configuration, not authority. The canonical organization registry, trusted assignment resolver, Guard, approval store and `ExecutionController` remain authoritative. A profile cannot grant a role, widen a task scope, add a tool permission, approve its own work, waive a gate or choose an unrestricted workspace.

## Current contract

The initial profile registry is `config/agent_profiles.v1.json`. It defines:

- `backend_implementer@1` — scoped implementation and targeted tests.
- `principal_architect@1` — architecture proposals and design review.
- `qa_reviewer@1` — test authoring, execution and findings.
- `security_reviewer@1` — security review and adversarial test design.
- `feature_delivery@1` — architect → implementer → QA and security review.

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

This implementation adds the typed registry, digest calculation, canonical role validation and dependency-checked team templates. Trusted plan dispatch can pin the exact profile ID, revision and digest into durable task intent. The assignment digest includes that pin, and the worker guard resolves and checks it again before handler execution, including after a fresh controller process resumes the task. A profile-role/action mismatch or stale digest fails closed. Profiles are still not enabled as an automatic team scheduler: independently enforced implementer/reviewer separation remains required before that activation. The supervisor persists profile-bound team-member identity and lifecycle state in its SQLite database. Registration is idempotent only for an exact member/profile revision/digest match; profile drift is rejected. Member transitions use compare-and-set state changes, and a fresh controller process can recover the persisted snapshot.

### Shared team-run budget

Registering a team also creates one supervisor-owned budget for the task/run. Its token, tool-call and wall-time ceilings are derived by summing the validated member profile defaults once, then applying hard run-wide caps (2,000,000 tokens, 10,000 tool calls and 86,400 wall seconds). These are shared pools, not per-member copies. Concurrent assignment is capped at four active members (or the team size when smaller).

Before an operation starts, its member reserves capacity under a stable operation key. The SQLite write transaction checks the aggregate of settled usage and outstanding reservations, so parallel members cannot spend the same remaining capacity. Repeating the same key and exact reservation is idempotent; conflicting reuse is rejected. Settlement requires a trusted host-configured usage verifier; caller-supplied numbers alone cannot settle a reservation. The verified metrics and canonical receipt digest are stored, and settlement is idempotent for the same receipt. An overrun is retained as evidence and blocks the member. Reservations with unknown outcomes remain held after restart. Capacity is released only when a trusted host-configured verifier accepts a host receipt proving the operation never started; the canonical receipt digest and reconciliation reason are stored with the release record. Without that verifier, reservations remain held.

This is a durable accounting and admission foundation. It does not yet wire every provider/tool adapter to reserve before external work, nor prove host-side resource metering. Automatic scheduling remains disabled until those adapters and independent implementer/reviewer enforcement are complete. Rollback: stop invoking the new team-budget APIs and retain the additive budget and operation rows for audit; do not delete or reset charges.

### Provider and tool adapter enforcement

The supervisor exposes `TeamBudgetAdapterEnforcer` in `portable/team_budget_adapters.py`. Pass the host-owned `team_budget_settlement_verifier` and `team_budget_release_verifier` into `AdaptiveRuntime` at construction. Supply `team_budget_enforcer_factory` to `AdaptiveRuntime`; the factory receives the initialized controller and returns the enforcer before graph workers can access the runtime. This keeps enforcer installation inside the trusted composition root. The factory creates the enforcer with two host-owned callbacks:

- `context_resolver` maps the active authenticated assignment principal to the canonical controller task, team run, member ID, stable operation key and bounded reservation amounts. It must ignore caller-supplied identity, budget and operation-key claims.
- `usage_evidence_resolver` returns the provider/tool host receipt for the completed call. The controller's separately configured `team_budget_settlement_verifier` must authenticate that receipt and return exactly the verified token, tool-call and wall-second metrics.

The composition root installs that enforcer for consequential provider dispatch. Pass the same enforcer to each `MCPStdioToolAdapter(team_budget_enforcer=...)` and `ApprovedToolRegistry(..., team_budget_enforcer=...)` for MCP and approved crew-tool calls. The direct `ExecutionController(team_budget_enforcer=...)` constructor argument remains available when the host creates the controller itself. An active authenticated assignment scope with no enforcer is denied before provider/tool dispatch. An adapter explicitly configured with the enforcer also denies calls when the assignment scope is absent, preventing context-loss bypasses. Non-assigned calls retain their existing path. A successful adapter result is not returned to the assigned worker until trusted usage settlement succeeds. A verified overrun blocks the member and denies success. Exceptions, timeouts, missing receipts and verifier failures keep the reservation held; do not refund an unknown operation. Reconcile it through the existing host receipt path.

`ExecutionController.team_delivery_release_readiness(task_id, run_id)` is the end-to-end acceptance check. It requires the supervisor task and every team member to complete, a completed QA/security profile, no unresolved or overrun budget operations, and a fresh host verification of the immutable approval evidence with reviewer principals distinct from the implementer. It reports readiness only; `release_authorized` remains false because a separate authorized release action is still required. A passing fixture test is not production release or host-isolation evidence.

Rollback: stop resolving the profile registry and retain the last-known-good configuration. Existing durable tasks and role assignments remain unchanged. Do not rewrite historical task evidence when a profile revision is retired.

## Validation

Run:

```bash
python -m unittest tests.test_agent_profiles tests.test_impact_policy
```

The production hardening workflow also runs `tests.test_agent_profiles`. These tests validate repository contracts only; they do not prove a deployed provider, workspace sandbox, memory isolation or independent reviewer.
