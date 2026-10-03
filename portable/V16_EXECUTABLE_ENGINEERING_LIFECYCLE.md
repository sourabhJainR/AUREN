# V16 — executable engineering lifecycle

AUREN treats review as a first-class evidence-bound gate and provides a provider-neutral team coordinator for splitting substantial work into independently complete units.

## Complete lifecycle

```text
research
  -> plan
  -> implement
  -> bounded feedback loop (observe -> act -> verify -> record)
  -> review
  -> shadow
  -> canary
  -> promote
       \-> rollback on failed observation/gate
```

## Loop-to-release evidence continuity

A bounded repair, research, or test loop is not an isolated mini-runtime. It uses the same `ProvenanceLedger` as the rest of AUREN.

When a `BoundedLoop` receives `provenance`, `run_id`, `context_evidence_digest`, and artifact IDs, it appends:

```text
loop.started
  -> loop.pass.completed (or loop.pass.approval_required / loop.pass.error)
  -> loop.completed
  -> shadow.started
  -> ...
  -> canary...
  -> promote | rollback
```

`LoopRunReceipt` carries:

- the immutable loop definition digest;
- the same `ContextEvidence.evidence_digest` used by engineering and rollout;
- the bounded pass results and evidence;
- the receipt digest;
- the final provenance record hash.

The final provenance record hash points to `loop.completed`. The next lifecycle event, such as `shadow.started`, uses that record as its parent through the ledger's normal hash chain. This makes a later rollout decision traceable back through the bounded work that produced the artifact.

The receipt digest excludes only the provenance pointer itself, so attaching the receipt to the ledger does not change the receipt's content identity. The evidence digest remains part of the receipt identity.

## Complete evidence chain

```text
ContextEvidence.evidence_digest
          |
          v
      loop.started
          |
          v
   bounded loop passes
          |
          v
    loop.completed
          |
          v
    verification/review
          |
          v
       shadow
          |
          v
       canary
          |
       +--+--+
       |     |
       v     v
    promote rollback
```

A loop may stop with `success`, `clean_no_op`, `blocked`, `approval_required`, `exhausted`, `no_progress`, or `error`. Loop completion never bypasses verification, review, regression, shadow, canary, promotion, or rollback gates.

## Existing lifecycle contracts

The implementation reuses the existing `agency_execution_plan`, `ContextEvidence`, `ArtifactStore`, verification, regression, provenance, and release contracts. `portable.feedback_loop.BoundedLoop` owns bounded loop execution; `.ai-harness/runtime/loop_engine.py` retains planning/scoring helpers and delegates execution to the canonical portable implementation.
