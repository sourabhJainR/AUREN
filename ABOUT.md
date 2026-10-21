# About AUREN

**AUREN — Autonomous Unified Reasoning & Engineering Network** is a provider-neutral AI engineering and orchestration system for software development. It coordinates work through explicit intent, repository context, bounded planning and execution, specialist proposals, verification, review, durable evidence, and guarded learning.

AUREN's goal is not to make an agent run indefinitely or to treat model output as authority. It is to make engineering work more inspectable, recoverable, and testable while limiting side effects to the authority and scope granted.

## How AUREN is organized

- **Core** — world/self models, reasoning, and goal definition.
- **Engine** — planning, StateGraph execution, agent teams, and resource routing.
- **Memory** — episodic, semantic, engineering, and evidence lineage.
- **Learning** — experimentation, causal attribution, capability invention, and curriculum evolution.
- **Arena** — independent evaluation, holdouts, transfer testing, and attestation.
- **Guard** — security, authority, verification, promotion, canary, and rollback controls.

These are ownership and trust boundaries, not a requirement to split AUREN into heavyweight services. The current implementation and each domain's canonical ownership are described in [Current Architecture](docs/ARCHITECTURE_CURRENT.md).

## Before implementation

AUREN's orchestration now records a structured pre-implementation review before graph nodes execute. It checks for common explicit requirement conflicts, missing observable acceptance criteria, vague success claims, and unverified assumptions. Unresolved explicit contradictions block dependent implementation before side effects; a documented resolution remains in the evidence record.

The review is intended to be collaborative, not prescriptive: it explains why a concern matters, asks a focused question, and offers choices with trade-offs or a narrower/reversible path. Independent, safe investigation may continue while a user-owned decision is unresolved.

Deterministic conflict patterns are limited and do not guarantee detection of every semantic contradiction. Planner/reviewer-supplied concerns and repository evidence remain important, and absence of a finding is not proof that a request is consistent.

## AI-era principles and evidence

AUREN maintains a canonical registry of 20 AI-era engineering principles covering intent, verification, authority, invariants, provenance, recoverability, bounded autonomy, context integrity, containment, independent challenge, accountability, guarded learning, targeted changes, handoffs, graceful degradation, untrusted sources, proof-required promotion, lifecycle cost, measurement, and context as an engineering dependency.

The registry and digest-bound runtime receipt improve traceability; they do not alone prove that every control is fully enforced. Repository CI validates repository-level contracts. Independent external evaluation, corpus provenance, separate isolation audits, and operator-run production migration/rollback rehearsals require their own evidence.

## Useful links

- [Repository README](README.md)
- [Current architecture](docs/ARCHITECTURE_CURRENT.md)
- [AI-era foundation principles](docs/AI_ERA_FOUNDATION_PRINCIPLES.md)
- [Engineering principles and task lifecycle](ENGINEERING_PRINCIPLES.md)
- [Engineering design policy](.ai-harness/ENGINEERING_DESIGN_POLICY.md)
- [GitHub Pages site](https://sourabhjainr.github.io/AUREN/)
- [Repository and current pull requests](https://github.com/sourabhJainR/AUREN)

## Current implementation status

The durable progress-journal foundation is merged in [PR #764](https://github.com/sourabhJainR/AUREN/pull/764), and the supervisor/controller plus dashboard projection are connected in [PR #766](https://github.com/sourabhJainR/AUREN/pull/766). The journal reports observed lifecycle transitions; it does not invent percentage completion, and terminal success is emitted only after the durable work-unit receipt commits. The CLI progress view merged in [PR #767](https://github.com/sourabhJainR/AUREN/pull/767) exposes the same journal via `python auren_cli.py progress --json`. PR [#770](https://github.com/sourabhJainR/AUREN/pull/770) adds a bounded-wait resumable provider executor; PR [#771](https://github.com/sourabhJainR/AUREN/pull/771) bounds in-process and cross-process journal lock acquisition; and PR [#773](https://github.com/sourabhJainR/AUREN/pull/773) applies a shared bounded-call boundary to both the resumable executor and `ProviderBackedCrewAdapter`, with a global eight-worker cap. PR [#775](https://github.com/sourabhJainR/AUREN/pull/775) adds allowlisted provider lifecycle projection; PR [#776](https://github.com/sourabhJainR/AUREN/pull/776) wires all specialist callbacks from a supplied supervisor-owned journal and propagates explicit supervisor task identity through graph dispatch. Host bootstrap must still pass the canonical journal and task ID. This bounds caller wait but cannot kill a timed-out Python thread. Cross-store crash reconciliation, cancellation/deadline/hard-termination fault injection, independent evaluation, external isolation review, and staging migration/rollback evidence remain explicit acceptance gates. See the [README](README.md) and [repository/website alignment review](docs/REPOSITORY_WEBSITE_ALIGNMENT_REVIEW_2026-10-20.md) for the latest revision and evidence boundaries.
