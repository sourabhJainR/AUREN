# Verification Policy

Verification is layered and evidence-driven.

## Evidence order

1. Direct acceptance-criteria evidence
2. Focused behavioral tests
3. Build/type/compile validation
4. Integration/system checks
5. Static analysis and linting
6. Diff and structural checks
7. Reviewer findings
8. Model self-report

Lower-ranked evidence never overrides failing higher-ranked evidence.

## Task scorecard

Every implementation should answer:

- What behavior changed?
- How is the requested behavior proven?
- What important failure path is covered?
- What contracts changed?
- What compatibility risk remains?
- What was not tested and why?
- Which material alternatives were considered, and why was this option selected?

## Verification failure

A failed check enters diagnosis, not blind retry. The repair phase must identify the failure, create a new hypothesis, change the approach, and rerun the smallest useful validation before broader validation.

Every retry must introduce new evidence, a changed hypothesis/strategy, or a newly evidenced environmental condition. Repeating an unchanged failing attempt is a non-progress state and must stop or escalate.

## Trust and option selection

Trust is scoped to the task family, repository snapshot, execution route and freshness of evidence. It is not a single universal score and must not transfer across repositories or task families. A successful result only increases confidence in the dimensions it actually tested.

For non-trivial work, compare viable options by acceptance coverage, risk/blast radius, reversibility, compatibility, maintenance cost, dependencies/permissions, verification quality, and time-to-evidence. Apply hard policy and safety constraints first; choose the smallest reversible option that meets the acceptance criteria with adequate evidence. Record why the nearest alternative was rejected. Use risk-proportionate verification and independent review where material.

See [Task Trust and Option Selection](../docs/TASK_TRUST_AND_OPTION_SELECTION.md) for the operational states, trade-offs, evidence record, and promotion/degradation rules.

## Acceptance

A run is ACCEPTED only when all mandatory checks pass and there is no unresolved critical/blocking reviewer finding. Reports must distinguish what changed, what was proven, what was observed, what remains unproven, and how to roll back.
