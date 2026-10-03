# GenAI Coding System Policy

This policy adapts the most useful engineering ideas from the `awesome-generative-ai-guide` into AUREN. It is an engineering policy, not a list of external tools.

## 1. Core model

AUREN treats an AI coding system as four cooperating capabilities:

```text
Agent Core + Memory + Tools + Planning
                 |
          Context Engine
                 |
      Execute -> Verify -> Review
                 |
              Outcome
```

The model is replaceable. AUREN owns the harness, context, evidence, controls, verification and learning.

## 2. Context engineering is first-class

Prompt wording alone is insufficient. Before each meaningful model call, AUREN should decide what the model needs to see.

Context selection order:

1. Repository and organization instructions.
2. Task contract and protected behavior.
3. Relevant source and tests.
4. Deterministic structural evidence such as AST, symbol and dependency relationships.
5. Relevant history and prior decisions.
6. Retrieved durable memory.
7. External documentation or research when explicitly needed.

Do not replay the repository, full graph, full transcript or full memory store.

Every context item should carry:

- source/provenance;
- freshness or revision;
- confidence;
- relevance reason;
- optional security classification;
- token/size cost.

## 3. Retrieval pipeline

AUREN should support progressive retrieval rather than a single vector-search dependency:

```text
Intent
  -> classify
  -> lexical/structural retrieval
  -> semantic retrieval when useful
  -> graph expansion when relationships matter
  -> rerank
  -> deduplicate
  -> budget
  -> context
```

Use hybrid retrieval where practical. Semantic retrieval is useful for concepts and intent; lexical retrieval is stronger for exact symbols, identifiers and error messages; graph retrieval is stronger for call paths, dependencies and impact analysis.

Reranking must consider task relevance, evidence quality, freshness, confidence and context cost.

If retrieval is weak, the agent must say so and gather more evidence rather than filling gaps with guesses.

## 4. Planning and decomposition

Hard work should be decomposed into small independently verifiable slices.

For each slice record:

- goal;
- inputs/evidence;
- files or components likely affected;
- acceptance criteria;
- verification command(s);
- risk;
- completion state.

Planning is required when the task is broad, cross-cutting, ambiguous, high-risk or likely to exceed one context window. Small settled changes may skip a visible plan.

## 5. Agent loop

The default loop is:

```text
UNDERSTAND
   -> RETRIEVE
   -> PLAN
   -> CHANGE
   -> VERIFY
   -> REVIEW
   -> PROVE
```

A retry must be evidence-driven. Repeating the same search, edit or test without new evidence is a stalled loop and must trigger a strategy change or stop.

## 6. Tool discipline

Tools are capabilities, not goals.

Before using a tool, AUREN should answer:

- What uncertainty will this tool reduce?
- What evidence will it return?
- Is there a cheaper/local source?
- What is the expected cost and risk?

Prefer deterministic repository tools before broad model exploration. Use MCP/external tools when they add material evidence or action capability.

Tool calls should be bounded by task risk and context budget.

## 7. Verification and evaluation

Evaluate the complete coding system, not only the model.

AUREN should track at minimum:

| Dimension | Examples |
|---|---|
| Task utility | accepted change, acceptance criteria passed |
| Correctness | tests, static analysis, behavioral checks |
| Robustness | malformed input, edge cases, retry behavior |
| Safety | policy violations, secret exposure, unsafe actions |
| Efficiency | model calls, tool calls, tokens, latency, context reuse |
| Maintainability | diff size, unnecessary files, complexity, conventions |
| Review quality | defects found before merge, false positives |
| Outcome | accepted/reverted, production result when available |

A passing test suite is evidence of verification, not proof that the engineering outcome was successful.

## 8. Independent verification

For meaningful changes, verification should use a fresh or minimized context that does not blindly inherit the author's reasoning.

The verifier receives:

- task contract;
- protected behavior;
- relevant diff;
- required evidence;
- verification results;
- repository rules.

Avoid giving the verifier the entire implementation narrative unless needed. This reduces confirmation bias.

## 9. Multi-agent policy

Do not use multiple agents by default.

Use specialized agents only when:

- the task naturally splits into independent domains;
- one context cannot hold the required evidence;
- adversarial review provides meaningful value;
- parallel work materially reduces time.

Otherwise a single well-controlled agent is preferred because multi-agent coordination adds latency, cost and failure modes.

## 10. Memory and learning

Separate:

- working memory: current task context;
- durable memory: validated cross-session facts and decisions;
- repository evidence: current source-of-truth facts;
- outcome memory: what happened after the change.

Only promote a memory item when it has sufficient evidence. A model suggestion, one-off failure or unverified preference must not become policy.

Learned behavior can improve retrieval and recommendations but cannot override security, permissions, approval boundaries or repository instructions.

## 11. Production readiness

When a model/provider is used repeatedly, record enough operational data to answer:

- Did the task succeed?
- How many model/tool steps were needed?
- What context was retrieved?
- What failed?
- What did verification catch?
- What did the user accept or reject?
- What did it cost and how long did it take?

Provider/model changes should be evaluated against a stable regression corpus before becoming the preferred default.

## 12. AUREN adaptation map

| GenAI concept | AUREN implementation direction |
|---|---|
| Context engineering | Context Engine + per-stage budgets |
| Agent harness | AUREN Control Plane |
| Memory | Second Brain + Engineering State Ledger |
| Tools/MCP | Capability Provider contracts |
| Planning | Spec + independently verifiable slices |
| RAG | Hybrid evidence retrieval |
| Reranking | Evidence ranking before context inclusion |
| Agent evaluation | Deterministic + regression + provider evals |
| Production/LLMOps | Cost, latency, calls, failures and outcome telemetry |
| Multi-agent | Explicit opt-in only |
| Safety | Policy gates, least privilege, approval boundaries |

## 13. Golden rule

Optimize for **verified engineering outcome per unit of model/tool cost**, not for maximum model autonomy and not for minimum token count alone.
