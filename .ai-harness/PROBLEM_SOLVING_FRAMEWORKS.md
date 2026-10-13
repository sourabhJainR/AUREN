# Adaptive Problem-Solving Frameworks

AUREN uses seven complementary problem-solving frameworks as a routing layer for every engineering task. They are not seven mandatory ceremonies. The orchestrator selects the smallest set that improves correctness, speed, evidence quality or risk control.

## Universal rule

Every non-trivial task must pass through Problem-Solving Selection before execution:

```text
INTAKE -> OBSERVE / ORIENT (OODA) -> CLASSIFY -> SELECT FRAMEWORK(S) -> PLAN -> EXECUTE -> MEASURE / VERIFY -> LEARN
```

Use repository evidence first. Frameworks structure reasoning; they never replace tests, source evidence, security controls, product requirements or human approval.

## The seven frameworks

### 1. OODA Loop

Observe -> Orient -> Decide -> Act. Use when the environment changes quickly, evidence arrives incrementally, or action speed matters. Typical uses: production incidents, urgent bug triage, changing dependencies/providers, and investigations where each observation changes the next action. Separate facts from assumptions, record uncertainty, choose the next valuable evidence, act, observe and loop within a bounded budget.

### 2. DMAIC

Define -> Measure -> Analyze -> Improve -> Control. Use for measurable existing behavior, recurring quality/performance problems, operational efficiency and continuous improvement. Typical uses: performance, flaky tests, CI reliability, token/tool/latency optimization and regression-rate improvement. Establish a baseline before changing the system and measure the result afterward.

### 3. Root Cause Analysis / 5 Whys

Start from the observed failure and repeatedly ask why until reaching an actionable technical or systemic cause. Branch when multiple causes are plausible. Typical uses: bugs, defects, recurring CI failures, regressions and unexpected model/tool behavior. Treat the root cause as a hypothesis until supported by reproduction, tests, logs, history or controlled comparison.

### 4. Pre-Mortem Analysis

Assume the proposed solution has failed and identify why before implementing it. Typical uses: new features, architecture changes, self-modification, releases, migrations, security-sensitive changes and large refactors. Convert important failure modes into mitigations, tests, rollback paths or explicit human decisions.

### 5. First Principles Thinking

Reduce the problem to verified constraints and fundamental truths, then rebuild without assuming the current implementation is the only valid design. Typical uses: POCs, architecture, unfamiliar technologies and performance ceilings. Challenge assumptions, but do not discard repository conventions or protected behavior without evidence.

### 6. Six Thinking Hats

Evaluate distinct perspectives: White = facts and missing evidence; Red = user/team concerns and intuition; Black = risks and constraints; Yellow = benefits and positive evidence; Green = alternatives; Blue = process, decision criteria and next action. Use for design reviews, competing options, ambiguous requirements and high-risk multi-stakeholder decisions. Perspectives are not facts.

### 7. Decision Tree Analysis

Map meaningful choices, conditions, uncertainty, outcomes and reversibility. Use for architecture choices, provider/build-buy decisions, remediation paths, release/rollback decisions and expensive experiments. Use ranges or qualitative confidence when probabilities are unavailable; never invent precision.

## Adaptive routing matrix

| Work type | Primary framework | Supporting framework(s) |
|---|---|---|
| Research / investigation | OODA + First Principles | Six Hats, Decision Tree |
| POC / experiment | First Principles + Pre-Mortem | Decision Tree, DMAIC |
| New development | First Principles + Pre-Mortem | Decision Tree, OODA |
| Feature implementation | OODA + Decision Tree | Pre-Mortem, DMAIC |
| Bug / defect fix | 5 Whys + OODA | Pre-Mortem, DMAIC |
| Recurring failure / incident | 5 Whys + DMAIC | OODA, Pre-Mortem |
| Performance / reliability | DMAIC | 5 Whys, OODA, Decision Tree |
| Refactor / maintenance | First Principles + Pre-Mortem | DMAIC, Decision Tree |
| Architecture / design | First Principles + Six Hats | Decision Tree, Pre-Mortem |
| Security-sensitive work | Pre-Mortem + Decision Tree | 5 Whys, Six Hats |
| Release / migration | Pre-Mortem + Decision Tree | DMAIC, OODA |
| Review / analysis | Six Hats + First Principles | 5 Whys, Decision Tree |
| Self-improvement / self-modification | Pre-Mortem + Decision Tree | DMAIC, 5 Whys, OODA |

## Selection rules

1. Always classify before acting: problem type, uncertainty, risk and time pressure.
2. Select a primary framework; add supporting frameworks only when they materially help.
3. Use OODA when new evidence can change the next action.
4. Use 5 Whys when symptoms may hide causes; branch for multiple plausible causes.
5. Use DMAIC when a measurable baseline and controlled improvement are possible.
6. Use Pre-Mortem before consequential changes.
7. Use First Principles when assumptions or existing patterns may constrain the solution.
8. Use Six Hats when multiple perspectives or groupthink could distort the decision.
9. Use Decision Trees when choices have materially different outcomes, uncertainty or reversibility.
10. A framework may be marked `not needed` with a reason. Do not manufacture analysis just to satisfy a checklist.

## Evidence contract

For each selected framework capture compact evidence in the Engineering State Ledger:

`FRAMEWORK | PURPOSE | KEY_FINDINGS | DECISION | EVIDENCE | NEXT`

Classify material statements as `Fact | Inference | Unknown | Hypothesis | Recommendation`.

For defects/incidents also retain:

`SYMPTOM | 5_WHYS | ROOT_CAUSE_CONFIDENCE | CONTAINMENT | CORRECTIVE_ACTION | PREVENT_RECURRENCE`

For consequential proposals also retain:

`PRE_MORTEM_FAILURES | MITIGATIONS | DECISION_OPTIONS | TRADEOFFS | ROLLBACK_TRIGGER`

## Completion gates

Before ACCEPT:

- the problem is precise enough to verify;
- important assumptions are explicit;
- applicable root causes are evidence-backed;
- consequential risks have mitigations or explicit acceptance;
- alternatives were considered when uncertainty is material;
- acceptance criteria are measurable where practical;
- verification results are recorded;
- generalizable lessons can enter the learning pipeline.

Naming a framework is not enough. Its use must change or validate the work in an observable way.

## Relationship to AUREN orchestration

The frameworks sit inside the existing lifecycle rather than replacing it:

```text
INTAKE -> PROFILE -> ROUTE -> PROBLEM-SOLVING SELECTION -> PLAN -> EXECUTE -> OBSERVE -> EVALUATE -> VERIFY -> REVIEW -> REPAIR -> ACCEPT -> LEARN
```

OODA supplies adaptive feedback. DMAIC supplies measurement discipline. 5 Whys supplies causal diagnosis. Pre-Mortem supplies proactive risk discovery. First Principles challenges assumptions. Six Hats broadens perspective. Decision Trees make uncertainty and tradeoffs explicit.
