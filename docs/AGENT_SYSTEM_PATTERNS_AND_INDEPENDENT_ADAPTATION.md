# Independent agent-system patterns for AUREN

**Status:** implementation guidance; no third-party runtime dependency is introduced.

AUREN may learn from other agent systems without inheriting their packages, control planes, provider requirements, or claims. The architecture below is expressed as AUREN-owned contracts and is testable with local fixtures, fake providers, temporary repositories, and deterministic fault injection.

## Patterns worth adapting

| Pattern observed in parallel systems | AUREN-owned adaptation | Non-negotiable guardrail |
|---|---|---|
| Grok Build separates context assembly, agent loop, tool dispatch, and extension points; supports local-first inference and configurable skills/plugins/hooks/subagents. | Keep a small canonical execution loop and typed tool registry. Make model/provider adapters replaceable, and expose capability discovery without granting authority. | Tool availability is not permission. Every consequential call still passes AUREN's authority and policy checks. |
| Persistent-agent products center continuing work rather than requiring every responsibility to begin and end as a chat turn. | Treat a durable work item as the primary unit: goal, plan, status, owner, budget, checkpoint revision, evidence, next action, and last-known-good state. Sessions become views/continuations of that item. | No fabricated progress. Unknown, stale, or unavailable state is marked unverified, not running or complete. |
| Agent control-plane designs separate harness, orchestrator/runtime, and observation/governance, and make policy, budgets, and audit trails visible. | Keep the model loop, execution environment, and AUREN governance gateway separate. Emit a structured event for every tool start/end, denial, retry, cancellation, checkpoint, and recovery. | Observability cannot itself grant authority; telemetry failures must not silently bypass safety gates. |
| Durable graph runtimes preserve state across interruption and allow pause/resume and approval boundaries. | Use AUREN's existing checkpoint/store and recovery ledger as the durable source of truth; standardize adapters instead of creating a parallel run database. | Resume only from integrity-checked state; use idempotency keys and shared attempt/deadline budgets; stop when progress is not made. |
| Specialist agents can work in parallel through isolated environments and curated tools. | Add explicit read/research, implementation, test, and review roles with scoped tool capabilities; use separate worktrees or isolated workspaces where supported. | Reviewers cannot merge/push by default; parallel workers do not share mutable task state without an ownership protocol. |
| Cross-session memory is useful when it preserves project-specific conventions and lessons. | Reuse AUREN's canonical memory/provenance graph. Keep source, timestamp, confidence, verification, evidence IDs, retention, and privacy class with each learning. | Retrieval failure is not equivalent to an empty result; unverified learning cannot become a trusted fact or authorize execution. |

## Canonical AUREN work-item contract

The work item is a durable state machine, not a chat transcript. Its implementation should retain these fields (or map them explicitly through an adapter):

- Stable `work_id`, project identity, goal digest, creation/update timestamps, and current revision.
- Explicit lifecycle stage and status, including `unverified`, `blocked`, `cancelled`, and terminal failure states.
- Plan/task graph, dependencies, idempotency key, current owner, delegated workers, and next eligible action.
- One shared monotonic deadline, attempt/retry budget, token/resource budget, and cancellation signal propagated to child work.
- Policy/authority decision, approval receipt if required, and a traceable event for every consequential operation.
- Checkpoint digest, last-known-good revision, pending side effects, recovery decision, and rollback/reconciliation status.
- Evidence references for tests, diffs, review, CI, and verification; status claims must be derived from these records.

Do not add another database or scheduler if an existing AUREN owner can safely provide the contract. Prefer adapters and migration tests over duplicated ownership.

## Required local acceptance tests

These tests do not need external providers or independent evaluators:

1. Private tasks never call a remote executor, including after local failures.
2. Malformed, credentialed, non-loopback, redirecting, or ambiguous local endpoints fail closed.
3. Every attempt consumes the shared budget; timeouts and cancellation stop work at supported execution boundaries.
4. A process restart resumes only from a valid checkpoint; repeated recovery without progress terminates.
5. An unknown stage or failed memory read is visibly unverified/unavailable, not empty/successful.
6. Tool permission denial is auditable and cannot be bypassed by a subagent, hook, extension, or retry.
7. Concurrent sessions cannot overwrite each other's checkpoints, budgets, or evidence.
8. Rollback preserves the last-known-good state and reconciles writes made after cutover.
9. A model/tool can return malformed or adversarial output without bypassing schema, authority, or evidence checks.
10. All tests run without a configured remote model, third-party orchestration service, or external evaluation account.

## Rollout order

1. Harden local endpoints and dashboard boundaries; test privacy policy and accurate status/attempt metrics.
2. Reuse `portable.runtime_recovery.RuntimeExecutionGuard` for model routing as well as graph/supervisor work, charging local and remote calls to the same attempt ledger; do not claim hard cancellation for callbacks that cannot be interrupted.
3. Define and enforce the durable work-item/event contract through existing checkpoint and evidence stores.
4. Wire specialist crews to explicit capabilities, isolated workspaces, and shared task budgets.
5. Connect memory retrieval health and evidence lineage to the control center.
6. Add fault-injection suites for crash/restart, provider outage, quota reset, cancellation, stale checkpoints, concurrent writers, and rollback.
7. Keep independent Arena campaigns as a separate evidence stream. They can strengthen claims, but normal product operation and repository-level acceptance must remain self-contained.

## Source patterns reviewed

These links are references for design ideas, not AUREN dependencies or proof that AUREN itself works:

- [Grok Build open-source announcement](https://x.ai/news/grok-build-open-source) — context assembly, tool dispatch, extensions, and local-first configuration.
- [Grok Build agent-mode guide](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/15-agent-mode.md) — persistent sessions, structured tool updates, and explicit permission modes.
- [OpenHands: The Software Agent Control Plane](https://hub.openhands.dev/blog/agent-control-plane) — separation of harness, orchestrator/runtime, control plane, budgets, policy, and audit.
- [LangGraph: Building an agent runtime from first principles](https://www.langchain.com/blog/building-langgraph) — durable, controllable execution as a first-class runtime concern.
- [Open SWE architecture](https://github.com/langchain-ai/open-swe) — durable dispatch, sandbox lifecycle, and scoped reviewer capabilities.

**Independence rule:** AUREN may adopt concepts, but must implement and own its schemas, policies, tests, checkpoints, evidence, recovery, and runtime. A reference project's tests, benchmarks, services, or availability are never acceptance prerequisites.
