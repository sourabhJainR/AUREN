# Active Information and Transfer Validation Plan

**Goal:** Close the remaining depth gaps where AUREN can select information actions and structural transfer candidates but cannot execute bounded validation and learn from the observed result.

## Constraints
- Caller-owned actions only; this layer never grants permissions or bypasses capability policy.
- Every action is bounded, deterministic in measurement, and auditable.
- Transfer candidates must be validated in the target project before being treated as reusable.
- Negative transfer is first-class evidence and must block unsafe promotion.

## Task 1: Active information loop
- Add a bounded loop that selects an InformationAction, executes a caller-supplied probe, measures before/after uncertainty, and returns a durable receipt.
- Preserve existing InformationPlanner API.
- Add tests for cost/risk gating, realized gain, and execution isolation.

## Task 2: Transfer validation loop
- Add a bounded validator that executes a caller-supplied test for a TransferCandidate.
- Record pass/fail and negative-transfer evidence using existing LearningTransfer storage.
- Add tests proving failed validation is preserved and successful validation is not treated as proof beyond supplied evidence.

## Task 3: Review and CI
- Inspect complete diff.
- Fix all review findings.
- Wait for every CI workflow on the latest head.
- Merge only when all workflows are green.
- Verify merged main before final audit.
