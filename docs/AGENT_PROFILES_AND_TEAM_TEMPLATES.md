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

This first slice adds the typed registry, digest calculation, canonical role validation and dependency-checked team templates. It does not silently activate profiles or change existing task dispatch. Before profiles are enabled for execution, a follow-up must bind the exact profile ID, revision and digest into durable task intent and revalidate that binding before worker resume. It must also prove profile-specific memory isolation and keep implementer/reviewer authority separate.

Rollback: stop resolving the profile registry and retain the last-known-good configuration. Existing durable tasks and role assignments remain unchanged. Do not rewrite historical task evidence when a profile revision is retired.

## Validation

Run:

```bash
python -m unittest tests.test_agent_profiles tests.test_impact_policy
```

The production hardening workflow also runs `tests.test_agent_profiles`. These tests validate repository contracts only; they do not prove a deployed provider, workspace sandbox, memory isolation or independent reviewer.
