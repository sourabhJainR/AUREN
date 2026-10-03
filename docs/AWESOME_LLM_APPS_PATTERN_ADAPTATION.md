# Awesome LLM Apps Pattern Adaptation

AUREN adopts selected engineering patterns from Shubham Saboo's `awesome-llm-apps` repository without importing its application frameworks or provider-specific runtime.

## Adopted

| Source pattern | AUREN adaptation | Why it matters |
|---|---|---|
| Multi-MCP Agent Router | `SpecialistRouter` | Route work to a specialist with only the tools it is allowed to use. |
| Deep Research Agent | `ResearchPlanner` | Split research into bounded independent questions that can run in parallel before synthesis. |
| Mixture of Agents | `MixtureOfAgents` | Compare independent outputs through an explicit judge instead of trusting one model response. |
| Self-Improving Agent Skills | `OneChangeOptimizer` | Generate one targeted change, evaluate it, keep only an improvement, and repeat within a bound. |
| Scope Creep Detector | `ScopeChecker` | Compare changed paths with task intent and flag dependency/CI/configuration drift. |

The source repository describes itself as a collection of open-source AI agents, agent skills and RAG applications. The useful lesson for AUREN is not to copy complete applications, but to extract reusable control patterns. The selected patterns complement AUREN's existing evidence, graph, verification, learning and provider-neutral architecture.

## Deliberately not adopted

AUREN does not copy UI applications, Streamlit frontends, hosted-service assumptions, API-key handling, voice agents, image-generation workflows, or provider-specific agent SDKs into the portable runtime.

AUREN also does not replace its existing graph orchestration, memory, evaluation, regression, MCP, or self-improvement implementations. The new module supplies missing composition patterns around those existing primitives.

## Runtime composition

```text
Intent / Contract
      |
      +--> Scope check --------------------+
      |                                    |
      +--> Specialist route -> bounded tools
      |
      +--> Research plan -> parallel evidence tasks
      |                         |
      |                         v
      +------------------> independent outputs
                                |
                                v
                         explicit judge
                                |
                                v
                         verified synthesis
                                |
                                v
                    one-change improvement loop
                                |
                                v
                       existing AUREN gates
```

All model calls, MCP calls, web access and mutations remain host callbacks. The portable layer only owns deterministic contracts, bounds and evidence shape.

## Safety properties

- Specialist routing never grants tools outside the specialist declaration or request allow-list.
- Critical-risk specialists are excluded when the request's maximum risk is lower.
- Research is bounded by an explicit set of tasks; no open-ended autonomous loop is introduced.
- Consensus requires an explicit judge and validates the returned agreement score.
- Self-improvement evaluates a candidate before accepting it and rejects non-improving changes.
- Scope analysis is advisory and does not authorize a mutation.
- Existing AUREN security, verification, regression, shadow, canary, promotion and rollback gates remain authoritative.
