# LangGraph pattern alignment

AUREN adopts selected LangGraph ideas as provider-neutral engineering-runtime primitives. It does not add a LangGraph dependency and does not replace the existing `GraphAgentTeam` scheduler.

| LangGraph idea | AUREN adaptation | Why it fits |
|---|---|---|
| Explicit graph state | `StateGraph` partial-state nodes | Makes lifecycle state explicit and auditable |
| Channels / reducers | Per-key reducer functions | Safe aggregation when independent read-only nodes finish in one step |
| Conditional edges | `add_conditional_edges` | Keeps routing decisions explicit and testable |
| Durable execution | Checkpoint after every completed superstep | A failed long-running task can resume from the last durable boundary |
| Interrupts | `interrupt_before` / `interrupt_after` | Supports human approval and controlled pauses without granting model authority |
| Retry policies | Per-node bounded `RetryPolicy` | Handles transient provider/tool failures without unbounded loops |
| Recursion limits | `max_steps` | Prevents accidental graph cycles from becoming runaway execution |
| Execution traces | Immutable `GraphEvent` / `GraphRun.trace` | Preserves evidence for review and regression analysis |
| Map/reduce execution | Same-snapshot multi-node supersteps + reducers | Parallel read-only analysis can converge into one state |

## Ownership boundary

`portable/agency_state_graph.py` is the low-level state-machine primitive. `.ai-harness/runtime/graph_agent_team.py` remains the higher-level agent-team scheduler, with `TaskPlan` as its dependency contract and `SharedTaskMemory` as its task-scoped memory.

This separation avoids introducing a second agent scheduler while giving AUREN a reusable state-transition primitive for lifecycle orchestration.

## Safety boundary

The state graph only orchestrates Python callbacks supplied by the host. It does not execute shell commands, select credentials, grant permissions, call an LLM, or modify repositories by itself. Those actions remain behind AUREN's existing provider, collaboration, verification, and policy boundaries.
