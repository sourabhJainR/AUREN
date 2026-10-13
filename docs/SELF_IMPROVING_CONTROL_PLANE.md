# AUREN as a Self-Improving Coding Control Plane

AUREN should improve the way it engineers software from observed outcomes, without becoming an uncontrolled self-editing agent.

## Architecture

```text
                         AUREN CONTROL PLANE

 Intent -> Contract -> Risk
              |
              v
       Context Planner
              |
     +--------+---------+
     |        |         |
   Repo     Memory   External
 Evidence   /History  Research
     |        |         |
     +--------+---------+
              |
       Plan / Execute
              |
       Verify / Review
              |
              v
           OUTCOME
              |
      +-------+--------+
      |                |
  Evaluation      Telemetry
      |                |
      +-------+--------+
              |
      Pattern / Failure
          Mining
              |
      Improvement Proposal
              |
       +------+------+
       |             |
 Regression       Safety
   Gate             Gate
       |             |
       +------+------+
              |
        PROMOTE / REJECT
              |
         Versioned policy
              |
          Next run
```

## Five learning loops

### 1. Correctness loop
Learn from verification failures, regressions, review findings, and accepted/rejected changes.

Example: repeated missing regression tests for a workflow should produce a proposal to add an explicit regression-test gate.

### 2. Context loop
Measure which evidence sources and context selections correlate with successful outcomes. Improve retrieval modes, ranking, budgets, and reuse rules.

The goal is not “more context”; it is higher-quality evidence per model token.

### 3. Strategy loop
Detect retries and non-progress. If repeated attempts do not add evidence, change retrieval, debugging strategy, tool choice, or workflow phase.

### 4. Efficiency loop
Track model calls, tool calls, latency, token cost, cache reuse, and rework. Prefer a strategy that reaches an accepted verified change with less total cost when quality is preserved.

### 5. Repository learning loop
Persist repository-specific engineering patterns, successful decisions, and validated lessons. Keep them separate from global policy so one repository cannot silently change behavior for another.

## Improvement lifecycle

Every candidate improvement has:

- stable proposal ID;
- category;
- explicit change;
- rationale;
- evidence task IDs;
- confidence;
- risk;
- promotion state.

The runtime implementation in `.ai-harness/runtime/self_improvement.py` generates proposals from repeated outcome signals and requires both regression and safety gates before marking a proposal executable.

## Safe self-improvement

AUREN can autonomously **observe and suggest**. Promotion is gated.

Never auto-learn:

- permissions or credentials;
- production access;
- security exceptions;
- repository/organization instruction overrides;
- approval or merge authority.

This keeps the system adaptive without allowing a bad outcome to become a permanent unsafe rule.

## Maturity path

**P0 — Observe:** outcome schema, telemetry, verification evidence.

**P1 — Suggest:** repeated-pattern detection and improvement proposals.

**P2 — Evaluate:** deterministic replay plus regression/safety gates.

**P3 — Promote:** versioned, auditable routing/context/verification policies.

**P4 — Roll back:** detect degradation and automatically disable a promoted improvement, subject to the same safety boundary.

**P5 — Portfolio learning:** compare workflows, providers, repositories, and task classes to learn which strategies work best under different constraints.

The key invariant is simple:

> AUREN may learn from engineering outcomes, but evidence must earn the right to change behavior.
