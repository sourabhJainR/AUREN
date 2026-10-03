# AI Coding Research Adaptation

AUREN should learn from the engineering system around the model, not only from model outputs. This note records the research/practice bytes currently adapted into the runtime and skill.

## Evidence lanes

### 1. System reliability, not model score alone

Recent reliability synthesis treats coding agents as systems composed of model, harness, execution state, retrieval, memory, permissions, evaluation and observability. AUREN therefore records trajectory, graph digest, environment fingerprint, evidence and evaluator outcomes and uses layered gates.

Adapted:
- durable execution trajectory
- environment fingerprint
- evidence ledger
- layered verification
- explicit safety boundary for candidate execution

### 2. Continual learning

SWE-Bench-CL frames software engineering as an evolving stream rather than isolated tasks and recommends measuring forgetting, forward/backward transfer and tool-use efficiency. AUREN therefore keeps regression families and learning signals instead of optimizing only the latest task.

Adapted:
- learning signal per run
- transfer key
- repair/failure/attempt metrics
- regression-family mindset
- protection against overfitting to one successful task

### 3. Continuous integration and maintenance

SWE-CI evaluates agents over long repository evolution and repeated CI rounds. AUREN therefore treats regression replay and repository-native validation as durable parts of the learning loop rather than a final test pass.

Adapted:
- repeated regression replay
- long-horizon maintenance evaluation
- acceptance plus maintainability signals
- learning from later regressions

### 4. Realistic requests

RealSWE reports that real user prompts are often short and informal and that desired behavior and motivation can materially affect performance. AUREN therefore builds a minimal task contract and preserves desired behavior/motivation instead of stuffing every available repository detail into context.

Adapted:
- GOAL / REQUIREMENTS / ACCEPTANCE contract
- desired behavior and motivation capture
- targeted evidence retrieval
- context economics

### 5. Refactoring and repository-scale work

Recent benchmark work emphasizes large refactoring, integration and maintenance because one-shot issue resolution can hide failures. AUREN therefore evaluates graph-level changes, compatibility, regression paths and structure, not just whether one test passes.

Adapted:
- graph digest
- architecture review
- compatibility checks
- characterization/invariant/metamorphic/property-based checks when useful

### 6. Environment and sandbox controls

Current coding-agent practice puts substantial weight on sandboxing, constrained execution, telemetry and explicit boundaries. AUREN follows the same separation: candidate source is statically inspected without execution; candidate behavior must execute inside the designated evaluation boundary before promotion.

Adapted:
- side-effect-free static candidate validation
- forbidden dangerous constructs in candidate source
- regression and safety gates
- atomic promotion
- rollback lineage

## Self-improvement rule

AUREN is allowed to improve its own executable orchestration:

`Outcome -> Learning Signal -> Candidate -> Static Validation -> Regression -> Safety -> Shadow -> Canary -> Promote -> Monitor -> Rollback`

The learning engine may change routing, graph topology, node selection, retry/repair strategy and retrieval strategy. It may not grant itself credentials, permissions or security exemptions.

## Current research references

- Jarmak, *Engineering Reliable Coding Agents: Evaluating and Operating the System Around the Model* (arXiv:2608.13867, Aug 2026).
- Joshi, Chowdhury, Uysal, *SWE-Bench-CL: Continual Learning for Coding Agents* (arXiv:2507.00014).
- Chen et al., *SWE-CI: Evaluating Agent Capabilities in Maintaining Codebases via Continuous Integration* (arXiv:2603.03823, Mar 2026).
- Kim et al., *RealSWE: A Compositional Evaluation of Coding Agents under Realistic User Requests* (arXiv:2608.27831, Aug 2026).
- Shi et al., *SWE-Bench ProMax: Benchmarking Agents on Large-Scale Multilingual Code Refactoring* (arXiv:2608.09802, Aug 2026).
- *SWE-bench-Live*, continuously refreshed agentic software-engineering benchmark with trajectory verification.
- OpenAI, *Running Codex safely at OpenAI* (May 2026).
- OpenAI, *The next evolution of the Agents SDK* (Apr 2026).
- OpenAI, *How OpenAI uses Codex*, including plan-first, structured issue-style requests, environment iteration and Best-of-N practices.

Research is used as engineering input, not as a claim that a particular technique is universally optimal. AUREN should continue to re-evaluate these practices against its own regression corpus and production outcomes.
