# AUREN Learning Engine

AUREN now has the core closed-loop machinery for self-improvement:

```text
observe -> learn -> candidate -> replay -> safety gate -> promote -> monitor -> rollback
```

## Components

- `learning_engine.py` groups verified outcomes by task class and strategy and proposes strategies that repeatedly succeed with low rework.
- `policy_registry.py` keeps policies versioned and auditable. Policies start as candidates and can become active or rolled back.
- `regression_replay.py` provides a deterministic replay contract. A candidate must preserve expected success and verification behavior across the regression corpus.
- `rollback_controller.py` detects material acceptance degradation or regression-rate increases.

## Control-plane rule

The learning engine may recommend behavior. It does not grant itself authority. Promotion must be performed only after replay and the caller's safety/approval gate.

## Next integration

Wire these primitives into the existing Engineering State Ledger and context planner so every completed task produces an observation, every promotion is tied to replay evidence, and active policies influence routing/context selection. Keep policy state repository-scoped unless explicitly promoted to a broader policy domain.
