# About AUREN

**AUREN — Autonomous Unified Reasoning & Engineering Network** is a provider-neutral operating system for an AI engineering company. It coordinates CEO/CTO direction, engineering management, architecture, backend and frontend implementation, independent QA, security/reliability, research/evaluation, and release operations through a supervised graph/crew runtime. Work is planned against explicit acceptance criteria, bounded by shared budgets and authority, verified independently, recorded as durable evidence, and improved through controlled experiments. Its long-term direction is stronger software-engineering intelligence (SI) and broader general-purpose capability, with progress measured through independent transfer and safety evaluation rather than self-declared AGI claims.

AUREN's goal is not to make an agent run indefinitely or to treat model output as authority. It is to make engineering work more inspectable, recoverable, and testable while limiting side effects to the authority and scope granted.

## How AUREN is organized

- **Core** — world/self models, reasoning, and goal definition.
- **Engine** — planning, StateGraph execution, agent teams, and resource routing.
- **Memory** — episodic, semantic, engineering, and evidence lineage.
- **Learning** — experimentation, causal attribution, capability invention, and curriculum evolution.
- **Arena** — independent evaluation, holdouts, transfer testing, and attestation.
- **Guard** — security, authority, verification, promotion, canary, and rollback controls.

These are ownership and trust boundaries, not a requirement to split AUREN into heavyweight services. The current implementation and each domain's canonical ownership are described in [Current Architecture](docs/ARCHITECTURE_CURRENT.md).

## Software team and capability research

See the [Software Team Operating Model](docs/SOFTWARE_TEAM_OPERATING_MODEL.md) for persona responsibilities, manager-to-leadership reporting, independent verification, and final-acknowledgement rules. New SI and broader capability experiments are developed in the independent [AUREN Capability Lab](https://github.com/sourabhJainR/firstTestproj); it is not a runtime dependency of AUREN.

## Before implementation

AUREN's orchestration now records a structured pre-implementation review before graph nodes execute. It checks for common explicit requirement conflicts, missing observable acceptance criteria, vague success claims, and unverified assumptions. Unresolved explicit contradictions block dependent implementation before side effects; a documented resolution remains in the evidence record.

The review is intended to be collaborative, not prescriptive: it explains why a concern matters, asks a focused question, and offers choices with trade-offs or a narrower/reversible path. Independent, safe investigation may continue while a user-owned decision is unresolved.

Deterministic conflict patterns are limited and do not guarantee detection of every semantic contradiction. Planner/reviewer-supplied concerns and repository evidence remain important, and absence of a finding is not proof that a request is consistent.

## AI-era principles and evidence

AUREN maintains a canonical registry of 30 AI-era engineering principles covering intent, verification, authority, invariants, provenance, recoverability, bounded autonomy, context integrity, containment, independent challenge, accountability, guarded learning, targeted changes, handoffs, graceful degradation, untrusted sources, proof-required promotion, lifecycle cost, measurement, and context as an engineering dependency.

The expanded registry makes deterministic behavior, minimal trusted execution, explicit terminal outcomes, migration reversibility, supply-chain integrity, honest operator feedback, accessibility, lifecycle ownership, and correctness-over-optimization explicit. The registry and digest-bound runtime receipt improve traceability; they do not alone prove that every control is fully enforced. Repository CI validates repository-level contracts. Independent external evaluation, corpus provenance, separate isolation audits, and operator-run production migration/rollback rehearsals require their own evidence.

## Useful links

- [Repository README](README.md)
- [Current architecture](docs/ARCHITECTURE_CURRENT.md)
- [AI-era foundation principles](docs/AI_ERA_FOUNDATION_PRINCIPLES.md)
- [Engineering principles and task lifecycle](ENGINEERING_PRINCIPLES.md)
- [Engineering design policy](.ai-harness/ENGINEERING_DESIGN_POLICY.md)
- [GitHub Pages site](https://sourabhjainr.github.io/AUREN/)
- [Repository and current pull requests](https://github.com/sourabhJainR/AUREN)

## Current implementation status

The durable progress-journal foundation is merged in [PR #764](https://github.com/sourabhJainR/AUREN/pull/764), and the supervisor/controller plus dashboard projection are connected in [PR #766](https://github.com/sourabhJainR/AUREN/pull/766). The journal reports observed lifecycle transitions; it does not invent percentage completion, and terminal success is emitted only after the durable work-unit receipt commits. The CLI progress view merged in [PR #767](https://github.com/sourabhJainR/AUREN/pull/767) exposes the same journal via `python auren_cli.py progress --json`. PR [#770](https://github.com/sourabhJainR/AUREN/pull/770) adds a bounded-wait resumable provider executor; PR [#771](https://github.com/sourabhJainR/AUREN/pull/771) bounds in-process and cross-process journal lock acquisition; and PR [#773](https://github.com/sourabhJainR/AUREN/pull/773) applies a shared bounded-call boundary to both the resumable executor and `ProviderBackedCrewAdapter`, with a global eight-worker cap. PR [#775](https://github.com/sourabhJainR/AUREN/pull/775) adds allowlisted provider lifecycle projection; PR [#776](https://github.com/sourabhJainR/AUREN/pull/776) wires all specialist callbacks from a supplied supervisor-owned journal and propagates explicit supervisor task identity through graph dispatch. Host bootstrap must still pass the canonical journal and task ID. PR [#777](https://github.com/sourabhJainR/AUREN/pull/777) adds startup reconciliation against the SQLite task row and executive receipt: mismatches are blocked, expired leases are recovering, and terminal journal projections can be corrected without deleting history. This does not replay handlers or resolve ambiguous external effects. Bounded waits cannot kill a timed-out Python thread. Expanded commit-boundary fault injection, cancellation/deadline/hard-termination testing, independent evaluation, external isolation review, and staging migration/rollback evidence remain explicit acceptance gates. See the [README](README.md) and [repository/website alignment review](docs/REPOSITORY_WEBSITE_ALIGNMENT_REVIEW_2026-10-20.md) for the latest revision and evidence boundaries.
