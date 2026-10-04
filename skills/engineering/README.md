# Composable Engineering Skills

These skills adapt useful engineering workflow patterns to this repository's existing AUREN architecture.

They are deliberately thin orchestration documents. Runtime execution, context retrieval, capability selection, bounded loops, provenance, verification, rollout, and visual documentation remain owned by the existing canonical contracts.

## Flows

`engineering-router` routes work to the smallest useful capability.

`grill-with-docs -> research -> to-spec -> to-tickets -> implement -> tdd -> code-review` is the normal evidence-to-ship path when the work warrants the full flow.

`prototype` provides a bounded experiment when measurement can resolve uncertainty before production implementation.

`diagnosing-bugs` is the bug-fixing on-ramp.

`resolving-merge-conflicts` handles active merge/rebase conflicts using repository impact evidence and existing verification gates.

`improve-codebase-architecture` and `codebase-design` handle architecture health and module-shape decisions.

`interactive-documentation` turns repository-backed architecture, workflows, sequences, data flows, and lifecycles into a portable interactive HTML document. It reuses canonical repository intelligence and evidence instead of creating a second graph.

`domain-modeling` keeps domain language and durable decisions coherent.

`retro` turns observed session failures into small, verifiable improvements to the engineering environment.

## Visual architecture reviews

`improve-codebase-architecture` produces bounded candidate data and can hand the selected evidence to `interactive-documentation` for a richer reader experience. The visual artifact is evidence for selection, not an execution instruction.

`interactive-documentation` produces a self-contained HTML artifact with search, focus, relationship tracing, theme switching, accessible keyboard navigation, curated views, evidence/source details, and print-friendly output. It has no CDN, telemetry, hosted viewer, or runtime service dependency.

## Phase boundaries and handoffs

`PHASE_BOUNDARIES.md` defines the five safe phase transitions: continue, clear, handoff, subagent, and compact. It is a decision contract, not another state machine.

The canonical handoff implementation remains `.ai-harness/runtime/collaboration.py`. It carries intent, phase transition, scope, non-goals, context evidence digest, repository snapshot, artifact/receipt IDs, parent provenance hash, verification state, and exact next action. `record_handoff(...)` writes a `phase.handoff` event to the existing `ProvenanceLedger`.

Handoffs are persisted under `.ai-harness/state/handoffs/` and must be validated against the expected intent and requested phase before consumption. Repository mutation requires fresh context after a stale snapshot is detected.

## Ownership rule

Do not duplicate the repository's planner, memory, context, repository graph, capability registry, feedback loop, provenance ledger, verification receipts, or release state inside these skills. Skills select and constrain work; canonical runtime contracts execute and document it.
