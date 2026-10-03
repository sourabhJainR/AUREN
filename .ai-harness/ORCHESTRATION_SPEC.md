# Orchestration Contract

This harness is a control plane for coding agents. The provider is replaceable; the engineering contract is not.

## Execution model

AUREN follows the useful progression from a single agent to a bounded loop, then to a graph, and finally to an orchestration control plane:

```text
AGENT
  -> bounded LOOP: plan -> act -> observe -> evaluate
  -> GRAPH: nodes + dependencies + routing + joins
  -> ORCHESTRATION: scheduling + budgets + evidence + policy + replay
```

A graph does not replace the local agent loop. Agentic nodes may contain their own bounded loop; deterministic functions, evaluators, routers, joins and human checkpoints can be graph nodes too.

## State machine

```text
INTAKE -> PROFILE -> ROUTE -> PROBLEM-SOLVING -> PLAN -> EXECUTE -> VERIFY -> REVIEW -> REPAIR (0..N) -> ACCEPT -> LEARN
```

A task may branch to RESEARCH, POC, GRILL, DEBUG, or ISOLATE before EXECUTE when risk or uncertainty requires it.

## Problem-solving routing

Every non-trivial task passes through adaptive problem-solving selection before mutation or substantive execution. The router classifies work by problem type, uncertainty, risk and time pressure, then selects the smallest useful set from:

- OODA Loop: adaptive, time-sensitive work and changing evidence
- DMAIC: measurable existing-process improvement
- 5 Whys / Root Cause Analysis: failures, defects and recurring symptoms
- Pre-Mortem: consequential changes and proactive risk discovery
- First Principles: assumptions, POCs and architecture redesign
- Six Thinking Hats: multi-perspective decisions and collaboration
- Decision Tree: uncertain choices, tradeoffs and reversibility

The selection is recorded as evidence. A framework can be marked `not needed` with a reason; no artificial analysis is required. Frameworks organize reasoning and do not override repository rules, security controls, acceptance criteria, tests or human approvals.

Detailed routing and evidence requirements live in `.ai-harness/PROBLEM_SOLVING_FRAMEWORKS.md`.

## Graph contract

Every graph must have:

- explicit node identity and kind;
- explicit dependencies; cycles are rejected;
- a deterministic scheduling order for equivalent inputs;
- a declared mutation boundary;
- bounded node and run attempts;
- evaluation criteria for outputs where correctness can be checked;
- failure propagation rules;
- a replayable evidence projection.

Independent read-only branches may run in parallel. Mutating nodes touching the same resource are serialized. A router may choose a path, but it cannot bypass policy, verification, approval, or security gates.

## Evidence contract

Every phase/node must produce a timestamp, status, input context identifier, output artifact, tool/provider result when applicable, and next-state decision. A model assertion is never sufficient evidence for VERIFY or ACCEPT.

For non-trivial work, the evidence projection also records the selected problem-solving framework(s), purpose, key findings, decision and next action. For RCA, retain symptom, causal chain, root-cause confidence, containment and corrective action. For consequential decisions, retain premortem failures, mitigations, options, tradeoffs and rollback trigger.

Evidence is append-only and content-addressed. Replay consumes evidence projections; replay does not silently re-execute provider actions.

## Evaluation and repair

Use:

```text
Generation -> Evaluation -> Repair -> Evaluation -> Stop
```

A repair is valid only when it changes the diagnosis, output, strategy, context, or tool selection. Blind retries are prohibited. Repair budgets are bounded per node and globally per run.

Verification outranks model confidence. Acceptance requires explicit acceptance evidence and repository-native validation where available.

## Self-improvement boundary

AUREN may observe outcomes and produce improvement candidates. It must not directly rewrite its executable orchestration, permissions, security policy, approval rules, or production authority from model output.

Promotion follows:

```text
observe -> candidate -> regression replay -> safety evaluation -> shadow/canary -> promote -> monitor -> rollback
```

A candidate is proposal-only until deterministic regression and safety gates pass. Later degradation must be able to roll the candidate back without changing historical evidence.

## Stop conditions

Accept only when acceptance criteria are satisfied, available repository-native validation passes, the final diff is appropriate, required reviews pass, and no critical or blocking finding remains.

Use BLOCKED when evidence is insufficient, required access is unavailable, or repair attempts are exhausted.

## Human escalation

Escalate policy, product-intent, irreversible-production, security-authorization, or other decisions that cannot be established from repository evidence.

## Run invariants

- one immutable task identity per run;
- intent digest is stable across retries, graph branches and handoffs;
- append-only phase/node evidence;
- learned memory needs repeated evidence before trust;
- memory cannot modify permissions or security policy;
- accepted runs retain enough evidence for independent replay and review;
- every stop has an explicit reason;
- budgets prevent unbounded autonomous execution;
- non-trivial work has an explicit problem-solving selection and rationale.

## Reference implementation

`portable/orchestration.py` provides dependency-free graph, bounded-loop, evaluation, repair, evidence, replay and proposal primitives. It is provider-neutral by design: provider adapters remain responsible for model/tool execution.
