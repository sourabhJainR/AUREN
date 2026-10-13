# GenAI Coding Adaptation

AUREN reviewed the current `awesome-generative-ai-guide` and selected only ideas that improve an AI coding system. The source emphasizes that modern agent systems are built from a model plus a harness containing memory, tools and planning; that context engineering is a core discipline; that retrieval has moved toward hybrid and agentic approaches; and that evaluation must cover the whole system rather than only the model.

## What AUREN adopts

### Context engineering

AUREN already has context-budget and evidence-oriented design. This adaptation makes context selection an explicit stage for every meaningful model call. The system should retrieve the minimum evidence needed for the current stage and attach provenance, freshness, confidence and cost.

### Hybrid retrieval

AUREN should not force every repository question through embeddings. Exact identifiers and errors benefit from lexical search, relationships benefit from AST/graph evidence, and conceptual questions can benefit from semantic retrieval. These sources should be merged, reranked and deduplicated before prompt construction.

### Planning as a control function

Planning is not a mandatory ceremony. AUREN should infer when decomposition is needed from task size, uncertainty, risk and expected context pressure. Each complex slice should have its own acceptance criteria and verification path.

### Evaluation of the harness

AUREN's existing deterministic/regression/provider evaluation model is extended with utility, robustness, safety, efficiency, maintainability, review quality and outcome measures. This keeps evaluation focused on accepted engineering results rather than model claims.

### Independent verification

The verifier should receive the contract, relevant evidence, diff and verification results without unnecessary access to the author's complete reasoning. This makes review more independent and useful.

### Multi-agent restraint

Multi-agent execution is supported as a future capability, but it is not the default. It should be selected only when decomposition, parallelism or adversarial review provides a measurable benefit over one agent.

### Production feedback

Provider-backed runs should retain operational measurements such as model/tool calls, latency, context reuse, failures, verification findings and user outcome. These measurements form the evidence base for future routing and provider selection.

## What AUREN deliberately does not copy

AUREN does not turn into a course/resource catalogue, require a particular model provider, require a vector database, force fine-tuning, or introduce multi-agent orchestration for ordinary coding tasks. Those would increase complexity without improving the core control plane.

## Resulting architecture

```text
User Intent
    |
    v
Contract + Risk Classification
    |
    v
Context Planner
    |
    +--> lexical search
    +--> AST / graph evidence
    +--> semantic retrieval
    +--> durable memory
    +--> external research when required
    |
    v
Rerank + Deduplicate + Budget
    |
    v
Plan / Implement / Tool Loop
    |
    v
Independent Verify + Review
    |
    v
Proof + Outcome
    |
    v
Evaluation + Validated Learning
```

The key change is that context, retrieval, planning, verification and learning become measurable control functions rather than implicit prompt behavior.

## Source

Adapted from the public `aishwaryanr/awesome-generative-ai-guide`, especially its current material on agent harnesses, context engineering, RAG/retrieval, evaluation and production LLMOps.
