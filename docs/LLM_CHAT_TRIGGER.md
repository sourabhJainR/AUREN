# LLM Chat AdaptiveRuntime Trigger

AUREN exposes a provider-neutral, fire-and-forget trigger for an LLM chat, agent adapter, MCP client, or other interactive caller that needs to start normal `AdaptiveRuntime` execution without waiting for the full orchestration lifecycle.

## Flow

```text
LLM chat / MCP tool call
        |
        v
adaptive_runtime_trigger
        |
        v
AdaptiveTrigger.trigger_adaptive_runtime()
        |
        +--> durable TriggerRuntime event
        |       kind + priority + payload
        |
        +--> immediate TriggerReceipt
        |
        v
bounded background dispatch
        |
        v
TriggerRuntime claim + lease
        |
        v
AdaptiveRuntime.run()
        |
        +--> verification / evidence
        +--> experience record
        +--> deferred learning
        v
TriggerRuntime completion + durable outcome
        |
        v
existing maintenance service
```

The trigger and the maintenance service are complementary. The trigger is an on-demand execution path; the OS service continues to own the scheduled last-day-of-month maintenance cycle. Neither creates a second memory store or scheduler. The maintenance service does not take ownership of chat-trigger semantics.

## Python API

```python
from portable.adaptive_trigger import AdaptiveTrigger

trigger = AdaptiveTrigger.for_runtime(runtime)
receipt = trigger.trigger_adaptive_runtime(
    task="Improve repository retrieval for authentication changes",
    project_root="/workspace/repo",
    context={"source": "llm-chat", "conversation_id": "chat-123"},
    priority="normal",
    event_id="chat-event-123",
)
```

The call returns a `TriggerReceipt` immediately after the execution intent is durably persisted. The default `fire_and_forget=True` schedules bounded background dispatch; the caller does not wait for `AdaptiveRuntime.run()` to complete.

For an adapter that only wants to enqueue work and let an existing host drain it:

```python
receipt = trigger.trigger_adaptive_runtime(
    task="Re-evaluate the API timeout strategy",
    project_root="/workspace/repo",
    context={"source": "llm-chat"},
    fire_and_forget=False,
)
trigger.dispatch_once()
```

The runtime-keyed adapter is reusable:

```python
trigger_a = AdaptiveTrigger.for_runtime(runtime)
trigger_b = AdaptiveTrigger.for_runtime(runtime)
assert trigger_a is trigger_b
```

A convenience entry point is also available:

```python
from portable import trigger_adaptive_runtime

receipt = trigger_adaptive_runtime(
    runtime,
    "Learn from this completed repository task and evaluate the strategy",
    "/workspace/repo",
    {"source": "llm-chat"},
)
```

## Provider-neutral MCP capability

The capability name is `adaptive_runtime.trigger`. Its input schema is:

```json
{
  "type": "object",
  "required": ["task", "project_root"],
  "properties": {
    "task": {"type": "string", "minLength": 1},
    "project_root": {"type": "string", "minLength": 1},
    "context": {"type": "object"},
    "priority": {"type": "string", "enum": ["high", "normal", "low"]},
    "event_id": {"type": "string", "minLength": 1},
    "max_attempts": {"type": "integer", "minimum": 1, "maximum": 16}
  }
}
```

The Claude Code plugin bundles this capability through its MCP server registration. The MCP server starts `portable/chat_capability.py`, which exposes the tool `adaptive_runtime_trigger` and maps calls to the same `AdaptiveTrigger` implementation. The core runtime therefore remains provider-neutral; Claude-specific configuration is only the host registration boundary.

## Status and recovery

Use `trigger.get_status(trigger_id)` or `runtime.trigger_runtime.get(trigger_id)` to inspect durable state. Status includes attempts, max attempts, priority, availability, claim lease information, completion time, diagnostic detail, and a bounded JSON outcome.

Claims use a finite lease. If a worker disappears after claiming an event, another dispatcher can reclaim the event after the lease expires. A live claim cannot be stolen, and completion remains ownership-checked by `claim_id`.

The trigger dispatcher requests only `kind="adaptive_runtime"`, so unrelated trigger kinds remain untouched. Due selection orders `high`, then `normal`, then `low`, followed by availability time and event ID for deterministic FIFO behavior inside a priority.

Pass a stable `event_id` when the chat/tool layer may retry a request. Reusing the same ID with the same payload and priority returns the existing durable trigger rather than creating duplicate work. Reusing it with different content fails closed.

## Safety boundaries

The trigger does not bypass AUREN verification, review, evidence, policy, or learning gates. Chat context is passed into the normal `AdaptiveRuntime.run()` path; it does not create a privileged execution path. The background dispatcher is bounded to a shared four-worker pool so many chat requests cannot create one unbounded thread per request.

The MCP adapter accepts only JSON-compatible tool arguments and persists only bounded execution metadata. It does not store arbitrary Python return values in SQLite.
