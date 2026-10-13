# Canonical Engineering Evidence Envelope

AUREN now has one immutable envelope for carrying lifecycle lineage across planning,
execution, verification, review, learning and release.

The envelope is an index of identifiers and digests. It does not copy evidence
claims and is not a second evidence store.

## Ownership

- Canonical evidence records: state/engineering-state.schema.json
- Evidence validation: portable/evidence_contract.py
- Lifecycle envelope: portable/engineering_evidence_envelope.py
- Context acquisition: .ai-harness/runtime/context_pipeline.py

## Shape

The envelope binds:

`task_id -> intent_digest -> repository_snapshot_digest -> context_plan_digest`

to canonical evidence references and, as work progresses:

`decisions -> changeset -> verification -> review -> regression -> release -> outcome`

Every envelope has a content-addressed `envelope_digest`. A lifecycle update
does not mutate the previous envelope. `bind()` returns a new immutable snapshot
whose `parent_envelope_id` points to the previous digest.

## Safety rules

1. Evidence claims remain in the canonical evidence ledger.
2. Evidence references must be unique.
3. A referenced snapshot cannot silently differ from the repository snapshot.
4. Lifecycle identifiers cannot be duplicated or empty.
5. A supplied envelope digest must match its content.
6. Context evidence can seed the envelope, but later lifecycle stages are added
   through explicit binding.
7. Existing execution, verification, review, promotion and rollback gates remain
   authoritative.

## Context integration

A ContextEvidence instance exposes `engineering_envelope()`, which creates the
initial canonical lifecycle snapshot from the context evidence digest, repository
snapshot, context plan and selected evidence IDs.

This creates the foundation for the next phase, the end-to-end evidence graph,
without introducing a second repository index or evidence database.
