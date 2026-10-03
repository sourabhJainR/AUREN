# V15 — Executable Release Lifecycle + AI Agent Book Alignment

V15 closes the engineering loop from execution trace through regression and into
an artifact state transition.

## Release API

```python
from portable.agency_release_lifecycle import ArtifactStore

store = ArtifactStore("~/.auren/releases")
artifact = store.stage("dist/auren-portable.zip", "candidate-123")
state = store.transition("canary", artifact, "regression passed; staged rollout")
```

The same operation can be driven directly from `PromotionDecision`:

```python
state = store.apply_decision(decision, artifact)
```

The runtime integration is:

```python
result = execute(
    task,
    worker,
    regression_plan=plan,
    release_store=store,
    release_artifact="dist/auren-portable.zip",
    release_artifact_id="candidate-123",
)
```

`result.release_state` records the executable transition.

## State semantics

- **shadow**: candidate is immutable and addressable, but `current` is unchanged.
- **canary**: candidate is staged in the canary channel, but `current` is unchanged.
- **promote**: candidate becomes `current`; shadow/canary pointers are cleared.
- **rollback**: previous promoted content-addressed artifact becomes `current` when available.

Every transition is appended to `release-history.jsonl`.

## AI Agent Book v2 coverage

The complete audit is in `docs/AI_AGENT_BOOK_V2_ALIGNMENT.md`.

Implemented in the portable core:

- context budgeting and stable-context digesting;
- episodic/semantic/procedural memory with confidence-aware conflict resolution;
- active tool discovery and execution policy;
- MCP-style JSON-RPC tool list/call contract;
- event dispatch and cancellation;
- evaluation confidence intervals and candidate selection;
- evidence-backed continual-evolution candidates;
- explicit multi-agent handoffs with context/evidence boundaries;
- trace -> regression -> release lifecycle.

Provider/hardware-specific capabilities remain explicit adapters: actual model
training, realtime voice, GUI/mobile/robot control, remote vector databases and
MCP transports. The core exposes the contract and safety boundary rather than
claiming those environments are present.
