# Engineering Episode

EngineeringEpisode is the canonical closed-loop record for substantial AUREN
engineering work. It connects the existing planner, evidence ledger, graph
runtime, verification, review, remediation, learning, resource routing and
release systems without replacing any of their ownership.

## Gate contract

INTAKE -> PLAN -> EXECUTE -> VERIFY -> REVIEW

A review with findings must go through:

REVIEW -> REPAIR -> VERIFY -> REVIEW

Successful completion requires verification, review, regression evidence and an
explicit outcome. A verified failure requires a failure class and at least one
DO/DON'T rule.

Every transition is immutable and content-addressed. parent_digest provides
lineage; evidence, verification, review, regression and learning IDs point to
the existing durable systems.

The episode is intentionally small. It is an orchestration contract, not a
second evidence database.
