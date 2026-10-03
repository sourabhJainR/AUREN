# Adaptive Problem-Solving Frameworks

AUREN uses seven complementary problem-solving frameworks as a routing layer for every engineering task.

Every non-trivial task passes through: `INTAKE -> OBSERVE/ORIENT -> CLASSIFY -> SELECT FRAMEWORK(S) -> PLAN -> EXECUTE -> MEASURE/VERIFY -> LEARN`.

Frameworks: OODA, DMAIC, 5 Whys/RCA, Pre-Mortem, First Principles, Six Thinking Hats, and Decision Tree Analysis.

## Routing

| Work type | Primary | Supporting |
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

## Rules

1. Classify problem type, uncertainty, risk and time pressure before acting.
2. Select a primary framework and only supporting frameworks that add value.
3. A framework may be `not needed` with a reason; never manufacture analysis.
4. Frameworks never override repository rules, security controls, acceptance criteria, tests or human approvals.
5. Framework use must change or validate the work in observable evidence.

## Evidence

For selected frameworks capture:
`FRAMEWORK | PURPOSE | KEY_FINDINGS | DECISION | EVIDENCE | NEXT`

For defects/incidents retain:
`SYMPTOM | 5_WHYS | ROOT_CAUSE_CONFIDENCE | CONTAINMENT | CORRECTIVE_ACTION | PREVENT_RECURRENCE`

For consequential proposals retain:
`PRE_MORTEM_FAILURES | MITIGATIONS | DECISION_OPTIONS | TRADEOFFS | ROLLBACK_TRIGGER`

## Completion

Before ACCEPT: the problem is verifiable, assumptions are explicit, applicable causes are evidence-backed, consequential risks have controls or explicit acceptance, material alternatives were considered, acceptance criteria are measurable where practical, verification is recorded, and generalizable lessons can enter learning.
