# Source update 1.0.6 — Engineering principles contract

Status: source version only. No release artifact has been published by this change. The latest published release remains 1.0.5.

## Changes
- Expand the canonical AI-era engineering principle registry from 20 to 30 entries.
- Add deterministic planning/state/artifact/evidence behavior, minimal trusted execution, explicit operation outcomes, safe migration and rollback, supply-chain verification, truthful operator feedback, accessibility, reversibility, lifecycle ownership, and correctness-over-optimization.
- Make these requirements explicit in the root engineering contract and public documentation.
- Upgrade the principle registry and receipt schema to v2 so old receipts cannot be mistaken for receipts over the expanded registry.
- Add regression assertions for registry completeness and receipt behavior.

## Verification boundary
Focused tests and exact-head CI must pass before merge. The catalogue and digest-bound receipt provide traceability, not proof that every deployment enforces every control. External signature/key management, accessibility audits, and production migration/rollback rehearsals require separate evidence.
