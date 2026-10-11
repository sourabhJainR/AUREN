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
  identifier. Internal CI alone cannot satisfy this status.

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
