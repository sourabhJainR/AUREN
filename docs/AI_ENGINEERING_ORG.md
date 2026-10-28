# AUREN AI Engineering Organization Contract

**Status:** proposed organizational contract; not a runtime authorization policy or production certification.

AUREN is intended to operate as an AI-native engineering organization. Named agents have stable designations, titles, expertise, reporting relationships, accountabilities, permitted action classes, prohibited actions, and approval boundaries. This is a contract for planning and evaluation; it does not by itself enforce access or authority.

The machine-readable registry is [`config/agent_org.v1.json`](../config/agent_org.v1.json). Regression checks are in [`tests/test_agent_org_contract.py`](../tests/test_agent_org_contract.py).

## Reporting structure

```text
Stakeholders and Investors
└── CEO-01 — Chief Executive Agent
    ├── FIN-01 — Finance and Investor Reporting Agent
    └── CTO-01 — Chief Technology Agent
        ├── SEC-01 — Security Engineer Agent
        └── EM-ENG-01 — Engineering Manager Agent
            ├── BE-01 — Senior Backend Engineer Agent
            ├── ARCH-01 — Principal Architect Agent
            ├── QA-01 — Senior QA Engineer Agent
            └── SRE-01 — Reliability Engineer Agent

Independent Governance Council (external assurance owner)
└── EVAL-01 — Independent Evaluation Agent
```

The evaluator is deliberately outside the implementation reporting line. A separate name or process is not sufficient proof of independence; custody, infrastructure, credentials, corpus and evaluator identity must be independently controlled.

## Operating contract

1. **CEO:** sets portfolio priorities and business outcomes; reports to stakeholders and investors using verified metrics.
2. **CTO:** owns technical standards, architecture governance, security posture and release recommendations.
3. **Engineering manager:** creates dependency-aware plans, assigns scoped work, manages shared budgets, and escalates blockers.
4. **Specialist agents:** backend engineering, architecture, QA, reliability and security deliver scoped artifacts and evidence.
5. **Independent evaluator:** checks pinned artifacts against independent criteria; cannot modify the candidate to make its own evaluation pass.
6. **Finance/reporting:** computes cost and ROI from retained records, including failures, skipped checks, rework and uncertainty.

## Runtime requirements before enabling this hierarchy

- Keep the canonical supervisor, `StateGraph`, and `ExecutionController` as the only execution authority. Do not create a competing orchestrator.
- Bind every task to a stable ID, role ID, manager, explicit scope, delegated authority, shared budget, deadline, checkpoint, durable status and next action.
- Enforce permissions at the real execution boundary. A role title, prompt, manager message, model confidence score or registry entry cannot grant access.
- Record waiting, working, blocked, failed and complete states distinctly. Completion requires the configured acceptance evidence.
- Keep side-effect intent and operation identity durable before dispatch. Unknown provider outcomes remain blocked until trusted reconciliation; retries are bounded and cannot silently duplicate consequential operations.
- Separate implementation, QA, security review and independent evaluation. A worker cannot approve its own consequential work.
- Derive CEO/CTO dashboards from durable task, test, provider, deployment and recovery records—not agent-written summaries.
- Preserve rollback owners, last-known-good revisions, evidence and a safe rollback procedure for every activated change.

## Initial acceptance gates

- Registry is valid JSON, role IDs are unique, reporting parents exist, and the hierarchy has no cycles.
- Each role declares expertise, accountabilities, allowed and prohibited action classes, and approval boundaries.
- CEO/CTO/manager roles cannot override mandatory gates; the evaluator is outside the implementation reporting chain.
- The existing controller remains canonical; this registry does not silently enable tools, adapters, model routes or extension packs.
- Exact-head CI and post-merge main checks are retained. Skipped checks remain skipped.
- Deployment enforcement, production isolation, independent evaluator custody and operator-run rollback remain **not verified** until separate evidence is attached.

## Rollback

This first slice is additive documentation and a descriptive registry. Revert the registry, documentation and its test together if the role contract conflicts with the runtime or repository governance. Do not enable runtime behavior based on this metadata alone.

## Status vocabulary

- **Implemented:** code or contract exists in the repository.
- **CI-verified:** the named contract passed on an exact revision.
- **Deployment-verified:** exercised in the intended deployment with retained operational evidence.
- **Independently verified:** separate evaluator or operator produced authenticated evidence.

No agent count, hierarchy diagram, green internal CI result, or role label establishes autonomous-company readiness by itself.


## Host-authorized assignment implementation — 2026-10-28

`portable/agent_org_runtime.py` adds an assignment preflight for the descriptive registry. It validates the registry digest and reporting graph, checks the requested role's direct reporting parent and declared action class, bounds and canonicalizes scope, and requires a host-owned authorizer to return literal `True` for the exact assignment digest. `submit_agent_task` persists the resulting immutable assignment envelope in the durable task payload through the existing `ExecutionController.submit` path.

The assignment envelope is not a tool permission. The host authorizer must authenticate the manager and enforce organization policy. Assignment schema v2 binds the role/action/scope to the effective Guard policy version and digest. The controller recomputes that fingerprint before every dispatch, including after restart, and denies assignments created under a stale policy. Legacy v1 assigned tasks remain durable but require explicit reauthorization; they are not silently upgraded or run under weaker policy. Unassigned legacy tasks are unchanged. ExecutionController remains responsible for handler classification, Guard policy, approvals, deadlines, cancellation, provider reconciliation, and evidence-required completion. Handlers must project only the task fields they need into provider prompts; assignment metadata must not be treated as user instructions or authority.

Regression coverage: `tests/test_agent_org_runtime.py`. It covers wrong manager, unknown role, disallowed action class, false/truthy/non-boolean authorization, authorizer exceptions, malformed scopes, duplicate registry keys, reserved metadata, and durable payload binding.

This is a first runtime assignment boundary, not full production organization enforcement. The remaining integration gate is to bind authenticated role identity and allowed resource scopes to the actual execution/tool authorization boundary and verify it after process restart. No local tests were run in the connector-only environment; exact-head CI must validate the change.

Rollback: revert the assignment module and its tests, then remove this section if the contract conflicts with the current runtime. The existing task submission path remains unchanged for callers that do not opt into assigned-agent submission.


## Dispatch-time assignment scope enforcement — 2026-10-28

portable/agent_execution_guard.py validates every assigned task at the controller's Guard preflight: schema, exact assignment digest and ID, current registry digest, reporting parent, action class, normalized relative resource path, and exact path membership. Assigned work without a resource selector fails closed. Tampered or stale envelopes and path traversal attempts are rejected before handler dispatch. Unassigned legacy tasks retain their existing behavior.

The persisted marker and SHA-256 digest are integrity fields, not proof of manager identity. ExecutionController therefore accepts an optional trusted-bootstrap `agent_assignment_verifier` callback and passes it the immutable, digest-bound assignment after restart. The public `AurenClient` SDK exposes the same callback and forwards it to the canonical controller. If an assignment is present and no verifier is configured, the verifier raises, returns anything other than literal `True`, or cannot reach its trusted identity/policy source, dispatch is denied. Production bootstrap must provide a verifier that reauthenticates the manager and checks current assignment policy from host-controlled data; never implement it by trusting the task payload or the marker alone. Unassigned legacy tasks do not require this callback.

Regression coverage: `tests/test_agent_execution_guard.py` covers missing, denying, failing, and immutable-request verifier behavior. Scope strings mean exact normalized relative paths, not directories or glob patterns. Assigned tasks also require an operator-owned, persisted `agent_action_class` on the registered handler; the assigned action class must match it exactly. Missing or mismatched handler metadata fails before dispatch, and a handler's action-class contract cannot be changed under the same durable handler identity. This check does not grant permission to bypass Guard, approval, exact-head CI, or evidence gates.

The operation-boundary scope checks now cover `portable/guarded_filesystem.py` and the synchronous `MCPStdioToolAdapter` call path. MCP dispatch checks every explicit `resource`, `path`, and `file_path` selector before authority resolution or process launch. Missing selectors, conflicting selectors, traversal, and out-of-scope paths fail closed. Tests assert denial-before-launch and retain an in-scope dispatch regression.

This is not an OS sandbox. Context-local scope enforcement does not constrain code that bypasses guarded adapters, starts an unpropagated thread/process, or uses direct filesystem APIs. Direct subprocess, provider, network, nested-tool and process-isolation enforcement remain separate gates. Do not claim full organization enforcement or production isolation until those boundaries are covered and independently tested.

Rollback: revert the controller verifier injection, dispatch guard, tests, impact-policy/workflow updates, and this section together. Existing unassigned task behavior is unchanged; assigned tasks must not be silently downgraded to unassigned to work around a denial.
