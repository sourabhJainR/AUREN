# Scheduled Curriculum Campaign Execution

Phase 21 binds the resource-aware schedule to external campaign execution.

The executor validates that a schedule belongs to the frozen curriculum,
constructs the exact scheduled target set, and delegates evaluation to the
external campaign orchestrator. Resource budgets are checked before execution.

This component does not create oracles, mutate capabilities, promote lifecycle
state, or bypass external evaluation. The schedule is a policy artifact; the
external orchestrator remains responsible for independent validation evidence.
