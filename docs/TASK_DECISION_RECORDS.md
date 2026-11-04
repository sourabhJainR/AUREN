# Machine-checkable task decision records

AUREN now has a small standard-library validator for consequential engineering
choices. It complements, rather than replaces, the canonical evidence ledger
and lifecycle envelope.

## Contract

Each record binds the task and repository identity to full base/head commit
SHAs, acceptance criteria, the selected option and rationale, viable rejected
alternatives, risk, dependencies, check commands/results/evidence IDs, known
limitations, retry history, and an actionable rollback trigger/action.

The JSON Schema describes the portable record shape. The Python validator adds
context-sensitive checks that JSON Schema alone cannot establish: repository
identity and head SHA must match live values supplied by the caller; passing
checks must bind to that exact head; repeated retries must change the
hypothesis/strategy or add evidence; and promotion requires explicit approval
from a caller-supplied authorized-approver set.

## Promotion boundary

- none: record the decision without promoting trust.
- provisional / verified_for_scope / repeatedly_reliable: require all recorded
  checks to pass on the expected head and require an authorized approver plus
  approval evidence.
- independently_corroborated: additionally requires separately controlled
  evaluator and corpus/oracle identifiers, a receipt digest, and a repeat-run
  identifier. Internal CI alone cannot satisfy this status: at least one passing check must be marked independent/external, and its evaluator/corpus lineage and receipt/repeat-run identifiers must be supplied.

The validator does not grant permissions, authenticate an approver on its own,
create signatures, or claim a sandbox. The caller must obtain authorized
approvers and live repository identity from a trusted control plane. Do not
treat a valid record as sufficient to bypass branch protection or required
reviews.

## Run regression tests

    python -m unittest tests.test_decision_gate -v

The architecture-integrity workflow runs these tests and compiles the new
module. No runtime dependency is added and no cross-repository coupling is
introduced.


## Versioned requirement and assumption decisions

`EngineeringDecisionStore` appends requirement-conflict resolutions, explicit assumptions and scope changes to the same SQLite database owned by `EngineeringPlanStore`. Each record binds the stable decision ID and revision to the exact plan digest, plan-run revision, options, selected option, rationale, rejected alternatives, affected task IDs and actor ID. The store rejects stale plan/run revisions, unknown task references, malformed alternatives and tampered history.

A retry after a committed write returns the same receipt. A changed decision appends a new revision linked to the prior record digest; it never overwrites history. If the plan run advances, prior decisions remain readable but report `current_binding=false`. A new decision must bind to the current plan/run revision.

The record's `affected_task_ids` identify work that requires planner review and possible replanning. Recording a decision does not silently rewrite task state or authorize execution. The canonical planner must create and persist the revised plan/run and dependency changes before dispatch. Independent, unaffected work may continue only when the host planner proves it cannot be invalidated by the decision.

Rollback: stop writing new decision records and retain the append-only table for audit. No plan, task, approval or execution record is deleted or rewritten.
