# AI Agent Book v2 alignment audit

Source reviewed: `bojieli/ai-agent-book/book-en`, current 2.0 structure, Chapters 1-10.

AUREN is intentionally provider-neutral and offline-capable, so this audit maps the
book's engineering mechanisms into reusable control-plane contracts rather than
copying provider-specific experiment repositories or model-training stacks.

| Chapter | Book capability | AUREN status | Implementation / boundary |
|---|---|---|---|
| 1 | Agent = model + context + tools; Harness, ReAct, constraints, verification, correction | Strong | `agency_runtime.py`, planning, bounded execution, evidence, verification, graph orchestration, provenance |
| 2 | Context engineering, prompt safety, skills, context compression, stable prefixes | Implemented | `agency_agent_capabilities.build_context`; existing codebase context/retrieval and skill packs remain the richer repository-aware layer |
| 3 | User memory, RAG, structured knowledge, graph retrieval, agentic retrieval, multimodal memory | Partial-to-strong | `MemoryStore` supplies episodic/semantic/procedural lifecycle and conflict handling; `agency_codebase_context.py` supplies graph-aware retrieval and bounded context. External vector DB/embedding providers remain adapters rather than hard dependencies |
| 4 | Perception/execution/collaboration/event tools, MCP, active tool discovery, execution security | Implemented contract layer | `ToolRegistry`, risk/permission policy, active discovery and `agency_mcp.py` JSON-RPC tool endpoint. Real MCP transports/providers remain host adapters |
| 5 | Coding Agent, filesystem, verification, code as meta-capability, long-running harness | Strong | AUREN portable runtime, coding orchestrator skills, artifact regression, work reports, graph-aware retrieval, verification/repair lifecycle |
| 6 | Async/event-driven interaction, safe points, cancellation/preemption, voice, Computer Use, robotics | Core runtime primitives | `EventRuntime` and safe-point contracts added. Voice/GUI/robotics require environment/provider adapters; AUREN does not pretend a local dependency-free runtime is a robot or browser |
| 7 | Evaluation environments, datasets, judges, statistics, observability, selection, simulation | Strong | V13/V14 trace/regression loop, datasets/experiments, LLM-judge callback contract, `agency_evaluation_science.py`, exporters and quality receipts |
| 8 | Pre-training/SFT/RL, tool-call internalization, reward design, sample efficiency | Contract boundary | `LearningSignal` and regression/evaluation data are usable as training/evaluation inputs. Actual GPU training is intentionally outside the portable control plane and should be supplied by training adapters |
| 9 | Continual evolution, learning signals, update carriers, candidate validation, canary, rollback, consolidation | Implemented | `EvolutionCandidate`, regression gates, executable artifact store and shadow/canary/promote/rollback lifecycle |
| 10 | Multi-agent collaboration, context sharing/isolation, peer/manager/decentralized structures | Implemented contract layer | `CollaborationGraph`, explicit handoffs, context-key and evidence-key boundaries. Provider-specific agent execution remains an adapter |

## Closed-loop release lifecycle

The previous implementation recorded `shadow`, `canary`, `promote`, and
`rollback` as decisions. V15 makes them executable local state transitions:

```text
code / skill / harness change
        |
        v
immutable artifact -- SHA-256 --> ArtifactStore
        |
        v
trace -> regression dataset/version/digest -> quality + hard gates
        |
        v
PromotionDecision
   |       |       |       |
 shadow  canary  promote rollback
   |       |       |       |
   v       v       v       v
channel channel current  previous promoted current
        \     |      |      /
         +----+------+-----+
              |
           history
```

`portable/agency_release_lifecycle.py` is deliberately content-addressed. The
payload is immutable; channel pointers are mutable. This prevents a semantic
version from silently changing underneath a release decision.

`agency_runtime.execute()` accepts an optional `release_store` and
`release_artifact`. When provided, the AUREN-owned regression decision is applied
to the artifact lifecycle and returned in `ExecutionResult.release_state`.

## What is deliberately not faked

Some book experiments depend on infrastructure that cannot be meaningfully
implemented as a dependency-free AUREN core:

- real model SFT/RL/pre-training;
- real-time speech models and audio I/O;
- desktop/mobile/robot hardware control;
- external vector databases and embedding providers;
- third-party MCP transports and remote tool servers;
- full agent societies or simulation environments.

For these, AUREN exposes stable contracts, policy boundaries, evidence capture,
regression hooks and adapter points. A provider implementation can plug into the
same interfaces without changing the safety and release lifecycle.

## V15 acceptance checklist

- [x] artifact is content-addressed and immutable
- [x] shadow does not replace `current`
- [x] canary does not replace `current`
- [x] promote replaces `current` and clears staged channels
- [x] rollback returns to the previous promoted artifact when one exists
- [x] rollback with no active artifact is recorded without fabricating a target
- [x] release state is returned with execution results
- [x] context budget is explicit and digestable
- [x] memory conflict resolution is confidence-aware
- [x] active tool discovery is bounded
- [x] tool execution is policy-gated
- [x] MCP-style JSON-RPC tool listing/call contract exists
- [x] event cancellation is supported
- [x] learning candidates require evidence
- [x] statistical evaluation summaries and candidate selection exist
- [x] multi-agent handoffs explicitly declare context/evidence boundaries
