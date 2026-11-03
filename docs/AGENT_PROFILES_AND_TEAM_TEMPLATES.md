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

Before an operation starts, its member reserves capacity under a stable operation key. The SQLite write transaction checks the aggregate of settled usage and outstanding reservations, so parallel members cannot spend the same remaining capacity. Repeating the same key and exact reservation is idempotent; conflicting reuse is rejected. Settlement is idempotent and records actual usage. An overrun is retained as evidence and blocks the member. Reservations with unknown outcomes remain held after restart. Capacity is released only when the caller supplies an explicit confirmation that the operation never started; the reason is stored with the release record.

This is a durable accounting and admission foundation. It does not yet wire every provider/tool adapter to reserve before external work, nor prove host-side resource metering. Automatic scheduling remains disabled until those adapters and independent implementer/reviewer enforcement are complete. Rollback: stop invoking the new team-budget APIs and retain the additive budget and operation rows for audit; do not delete or reset charges.

Rollback: stop resolving the profile registry and retain the last-known-good configuration. Existing durable tasks and role assignments remain unchanged. Do not rewrite historical task evidence when a profile revision is retired.

## Validation

Run:

```bash
python -m unittest tests.test_agent_profiles tests.test_impact_policy
```

The production hardening workflow also runs `tests.test_agent_profiles`. These tests validate repository contracts only; they do not prove a deployed provider, workspace sandbox, memory isolation or independent reviewer.
