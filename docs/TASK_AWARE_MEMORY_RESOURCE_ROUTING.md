# Task-aware memory and resource routing

AUREN's task-aware routing primitives live in `portable.task_aware_routing`. They are provider-neutral and intentionally reuse the canonical `MemoryOrchestrator`; they do not create a second memory database or capability catalog.

## Memory routing

Use `plan_memory(task, task_type=...)` to create a bounded, inspectable memory plan. Explicit task type wins over deterministic keyword classification. Supported task types are conversation, coding, debugging, research, planning, decision, operations, and review.

Use `recall_for_task(orchestrator, task, ...)` to apply the plan through the canonical orchestrator. `AdaptiveRuntime.pack_memory_context` and `pack_memory_context_chunks` now use this route, so existing bounded context packing receives task-specific memory kinds without changing the canonical store. Required memory IDs are fetched by exact ID independently of query-term overlap, under the same project, intent, branch and revision scope. Missing, retracted or out-of-scope IDs still fail closed, and required content must fit in full within the budget. Callers can supply task/intent/branch/revision scope and opt in to short-lived sensory or reflective memory. Defaults intentionally omit sensory, reflective and KV-cache memories unless they are requested by the task policy.

## Resource routing

Call `select_resources` with bounded, read-only `ResourceCandidate` metadata collected by existing discovery adapters. The selector applies availability, permission, privacy, risk, network, sandbox, capability, and caller-supplied cost/latency constraints before ranking. A hard budget rejects candidates whose required measurement is unknown. When measurement is optional, unknown quality is neutral rather than evidence of quality.

The result is a recommendation only. It never installs, invokes, authorizes, or executes a tool, plugin, model, skill, API, or MCP server. The existing execution boundary must enforce policy again immediately before dispatch.

## Receipts and learning

`make_receipt` records a task digest, selected memory IDs/kinds, budget and omissions, plus resource decision reasons and policy digest. It deliberately excludes task text and memory content. The caller should send this receipt through the existing runtime/evidence audit owner and attach a verified outcome before any learning update. Do not treat a routing receipt alone as proof of task success.

## Production integration gates

- Invoke routing from the canonical graph/crew stage context builder, not from a parallel orchestrator.
- Supply live resource metadata from existing discovery adapters; do not infer availability from an installed descriptor.
- Enforce identity, approval, scope, network and sandbox policy again at dispatch.
- Persist receipts through the canonical audit/evidence owner with idempotency keys and redaction.
- Compare against the existing route on paired tasks: task success, missed-signal rate, cost, latency, omissions and uncertainty.
- Keep default changes behind a rollbackable configuration until regression and holdout evidence passes.

This change wires task-aware memory selection into the runtime's bounded context packers and exposes `AdaptiveRuntime.route_task_context` for combined memory/resource decisions. It does not claim every direct graph/crew dispatch consumes resource decisions, nor that external production isolation has been verified.
