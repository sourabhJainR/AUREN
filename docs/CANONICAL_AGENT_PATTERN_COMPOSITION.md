# Canonical agent-pattern composition

AUREN uses the following composition rule for agentic work:

```text
Intent / Contract
      |
      v
Scope + capability policy
      |
      v
Specialist routing
      |
      +--> one specialist when one capability owner is sufficient
      |
      +--> bounded specialist set-cover when capabilities span roles
      |
      v
TaskPlan / StateGraph
      |
      +--> independent read-only work -> bounded parallel wave
      +--> dependent work -> next dependency wave
      +--> mutation -> serialized behind dependencies
      |
      v
Evidence + shared task context
      |
      v
Independent outputs
      |
      v
Explicit judge / reviewer
      |
      v
Verification -> Review -> Shadow -> Canary -> Promote
      |
      v
Evidence-backed learning / one-change improvement
```

## Ownership rules

- `portable.agent_capabilities` remains the canonical capability and durable-memory owner.
- `portable.agency_state_graph` remains the canonical low-level graph executor.
- `portable.task_planner.TaskPlan` remains the canonical dependency contract for agent work.
- `.ai-harness/runtime/graph_agent_team.py` remains the high-level role orchestration layer.
- `portable.agent_patterns` contains deterministic algorithms only. It does not own providers, tools, durable memory, graph execution, or promotion policy.
- Compatibility modules may adapt APIs but must not create parallel runtime owners.

## Routing

Single-specialist routing fails closed when the requested capability set cannot be fully covered. `route_many` provides a bounded greedy set-cover algorithm for tasks whose capabilities legitimately span multiple specialists. Every selected specialist remains subject to the request risk ceiling and tool allow-list.

## Research

Research is represented as bounded tasks with optional dependencies. The planner produces deterministic topological waves. Independent questions can run concurrently; dependent questions wait for their prerequisite wave. Cycles and budget overruns fail closed.

## Synthesis

Independent agent outputs never become an authoritative result by themselves. A host-supplied judge must select or synthesize the result. Evidence is explicit and confidence-bounded. Verification and review remain authoritative after synthesis.

## Improvement

Self-improvement is one targeted change at a time. A candidate is retained only when evaluation improves. No change is treated as a promotion decision; existing regression, shadow, canary and release gates remain authoritative.

## Scope checking

Scope checking is advisory. Dependency and CI/configuration changes are surfaced as potential drift, while the authoritative engineering-design and execution policies decide whether they are acceptable.

## Why this composition

The source patterns that inspired this layer are useful because they improve decomposition, parallelism, specialization, synthesis and iterative quality. AUREN adapts those ideas as small deterministic contracts instead of introducing another agent framework. This keeps the existing state graph, capability fabric, evidence lineage, verification gates and release controls as the single execution and safety backbone.
