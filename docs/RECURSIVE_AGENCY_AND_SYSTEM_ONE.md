# Recursive Agency and Machine-Native Decisions

This repository is moving from an agent wrapper toward an evidence-driven adaptive control plane. The goal is not to claim AGI. The goal is to implement the useful engineering properties associated with increasingly general autonomous systems while keeping authority, safety and verification explicit.

## 1. What was added from the recent ideas

The design combines three useful directions:

1. **Loop engineering**: autonomous work is a bounded cycle rather than a one-shot prompt. Discovery, execution, verification, persistence, scheduling and optimization are explicit control points. The loop must be able to say no, stop, retry only with new evidence, and leave durable state.
2. **Graph engineering**: work is represented as small typed jobs connected by real dependencies. Independent work can run in parallel, while state and evidence converge at explicit synthesis and verification nodes.
3. **Machine-native decisions**: not every model call should generate prose. The `decision_fabric` exposes `Choice`, `Score` and `Noul`-style typed judgments with probabilities and confidence. Deterministic policy code decides whether a judgment is sufficient to authorize the next software action.

The TypeSafe announcement describes this separation as unstructured state in and typed probabilistic decisions out, with parallel evaluation of independent questions. Those are useful architectural properties; AUREN does not claim to reproduce TypeSafe's model architecture or RLCD training method. See the source discussion at https://typesafe.ai/blog/introducing-system-one-models-and-jev.

## 2. The resulting control architecture

```text
                    +-----------------------------+
                    | Persistent world / repo     |
                    +--------------+--------------+
                                   |
                              DISCOVER
                                   |
                          typed WorkItem graph
                                   |
                    +--------------v--------------+
                    | PLAN / ROUTE / PRIORITIZE   |
                    +--------------+--------------+
                                   |
                    +--------------v--------------+
                    | PARALLEL SPECIALIST WORK    |
                    | read / research / inspect   |
                    +--------------+--------------+
                                   |
                         BUILD / ACT / TOOLS
                                   |
                    +--------------v--------------+
                    | VERIFY + INDEPENDENT REVIEW |
                    +--------------+--------------+
                                   |
                    +--------------v--------------+
                    | DECISION FABRIC              |
                    | choice / score / yes-no      |
                    | probabilities + confidence   |
                    +--------------+--------------+
                                   |
                    +--------------v--------------+
                    | DETERMINISTIC POLICY GATE   |
                    +------+-----------------------+
                           |
                 +---------+----------+
                 |                    |
               accept               reject
                 |                    |
          REMEMBER / LEARN       REPAIR / RESEARCH
                 |                    |
             SCHEDULE  <--------------+
                 |
             OPTIMIZE
                 |
              RECURSE
                 |
              DISCOVER
```

## 3. Why the decision layer matters

Text generation is a poor interface for software when the application only needs a bounded judgment. AUREN therefore separates:

- **generation** for plans, explanations, code and research;
- **decision** for routing, risk, verification triage, prioritization and other bounded judgments;
- **policy** for permissions, thresholds and side effects.

A decision result is never permission by itself. For example:

```text
model:  risk = HIGH, confidence = 0.91
policy: high-risk changes require security review
system: route to security review
```

The model supplies evidence about a fuzzy question. Code remains responsible for authority.

## 4. Calibration is a first-class contract

A confidence value is useful only if it is measured. Providers may expose confidence, but AUREN should maintain a calibration ledger containing:

- question definition and version;
- model/provider/version;
- prediction probabilities;
- eventual verified outcome;
- calibration error and coverage statistics;
- task family and risk tier;
- whether a human overrode the result.

Future providers can then be compared by decision quality and calibration, not only by text quality.

## 5. RecursiveAgency

`portable.recursive_agency.RecursiveAgency` implements the control pattern:

```text
DISCOVER -> BUILD -> VERIFY -> REMEMBER -> SCHEDULE -> OPTIMIZE -> RECURSE
```

Properties:

- bounded cycle budget;
- dependency-aware work selection;
- explicit verification before completion;
- persistent learning callback;
- scheduler callback for remaining work;
- optimizer callback for strategy improvement;
- approval boundary for sensitive work;
- fail-closed error handling;
- deterministic receipt digest.

The controller does not create permissions, mutate policy, or declare itself successful without verification.

## 6. How this fits the existing AUREN implementation

These are complementary layers, not replacement runtimes:

| Concern | Canonical AUREN construct |
|---|---|
| dependency graph | `TaskPlan` + `StateGraph` |
| bounded graph execution | `agency_state_graph` |
| multi-agent roles | `GraphAgentTeam` |
| shared working memory | `SharedTaskMemory` / `ContextEngine` |
| durable memory | `PersistentMemory` / `AgentMemory` |
| long-term synthesis | `DreamMemory` / learning steward |
| bounded self-check loop | `BoundedLoop` |
| machine-native judgments | `DecisionFabric` |
| deterministic authority | `DecisionPolicy` + safety gates |
| autonomous cycle | `RecursiveAgency` |
| scheduling | `AutomationScheduler` |
| evidence lineage | `ProvenanceLedger` |
| repository world model | `RepositoryMap` / `RepositoryIntelligence` |
| regression learning | feedback, replay, shadow and canary gates |

Do not create a second graph, memory store or learning owner when extending the system. New behavior must adapt to these boundaries.

## 7. What this means for an AGI-oriented trajectory

The practical path is to increase **generality of the control loop**, not to add a large collection of agent personas.

The important capabilities to keep strengthening are:

1. **World modeling**: maintain a durable, queryable model of repository, task, environment, dependencies, outcomes and uncertainty.
2. **Goal decomposition**: turn open-ended objectives into typed work items with acceptance criteria and dependencies.
3. **Parallel cognition**: run independent investigation and candidate generation concurrently.
4. **Verification**: treat external evidence, tests, execution results and independent review as stronger than model assertions.
5. **Uncertainty**: carry probabilities, confidence and unknowns through the graph instead of collapsing everything to true/false.
6. **Long-horizon memory**: retain reusable lessons and failures without stuffing old transcripts into every prompt.
7. **Self-improvement**: modify strategies and harness behavior through replay and held-out regression evidence, not by blindly trusting self-evaluation.
8. **Environment interaction**: make tools and external systems typed, permissioned and observable.
9. **Recovery**: checkpoint, resume, isolate mutations, retry only when safe, and roll back when promotion gates fail.
10. **Resource awareness**: route simple judgments to fast decision paths and reserve expensive reasoning for tasks that need it.

The moat is the compounding evidence and control fabric around models: state, graph, memory, verification, calibration, regression history and safe recursive improvement.

## 8. Important non-adoptions

Do not copy claims merely because they sound AGI-like.

- AUREN does not claim to be AGI.
- `DecisionFabric` is not a new foundation model.
- A probability returned by a model is not proof of correctness.
- Parallel agents do not automatically improve quality.
- Self-generated learning is not promoted without evidence.
- More recursion is not better; every loop has explicit budgets and stopping conditions.
- The harness does not pretend to implement model-level KV caching, RLCD, parallel neural sampling or other provider-internal mechanisms it does not own.

The design target is measurable improvement in task completion, verification quality, recovery, cost, latency and transfer to unseen tasks.
