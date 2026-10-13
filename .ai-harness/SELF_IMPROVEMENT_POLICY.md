# AUREN Self-Improvement Policy

AUREN is a self-improving coding control plane when improvement is treated as a measured feedback loop, not unrestricted self-modification.

## Closed loop

```text
Intent
  -> Contract / Risk
  -> Context + Plan
  -> Execute
  -> Verify
  -> Review
  -> Outcome
  -> Evaluation
  -> Improvement proposal
  -> Regression + Safety gates
  -> Promoted policy / retrieval rule / workflow rule
  -> Next run
```

## What AUREN learns

AUREN may learn from verified outcomes:

- recurring failure classes and missing verification steps;
- retrieval sources that improve correctness for a workflow;
- context-selection and evidence-budget patterns;
- retry/thrash patterns that should trigger a strategy change;
- tool-selection patterns that reduce unnecessary calls;
- review findings and escaped regressions;
- repository-specific engineering patterns;
- provider/model performance by task class.

## What AUREN must never learn automatically

- permission escalation;
- credential or secret handling rules;
- production access;
- repository/organization instruction overrides;
- approval requirements;
- security policy exceptions;
- automatic merge/release authority.

## Promotion rule

An observation is not a policy. A proposed improvement must have:

1. reproducible evidence from one or more outcomes;
2. an explicit change and rationale;
3. regression evaluation against known-good behavior;
4. safety/policy evaluation;
5. an auditable record of promotion or rejection.

The implementation is intentionally provider-neutral and dependency-free. A model may suggest an improvement, but deterministic gates decide whether it can become executable behavior.

## Optimization target

Do not optimize for autonomy, token count, or model calls in isolation. Optimize:

**verified accepted engineering outcome / total cost and latency**

while preserving correctness, safety, maintainability, and review quality.

## Learning tiers

- **Observe:** record outcomes and evidence.
- **Suggest:** generate candidate improvements.
- **Evaluate:** replay deterministic and regression checks.
- **Promote:** enable only gated, auditable improvements.
- **Roll back:** disable a promoted rule when later evidence shows degradation.

The default runtime may observe and suggest. Promotion remains explicitly gated.
