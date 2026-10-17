# AUREN Product Architecture

**AUREN — Autonomous Unified Reasoning & Engineering Network**

```text
AUREN
│
├── AUREN Core
│   ├── World Model
│   ├── Self Model
│   ├── Reasoning
│   └── Goal System
│
├── AUREN Engine
│   ├── Planner
│   ├── StateGraph
│   ├── Agent Team
│   └── Resource Fabric
│
├── AUREN Memory
│   ├── Episodic
│   ├── Semantic
│   ├── Engineering
│   └── Evidence Graph
│
├── AUREN Learning
│   ├── Experimentation
│   ├── Causal Attribution
│   ├── Capability Invention
│   └── Curriculum Evolution
│
├── AUREN Arena
│   ├── Independent Evaluation
│   ├── Holdouts
│   ├── Transfer
│   └── Attestation
│
└── AUREN Guard
    ├── Security
    ├── Authority
    ├── Verification
    ├── Canary
    └── Rollback
```

## Canonical ownership

**Core** owns cognition, world/self models, reasoning and goals.

**Engine** owns decomposition, planning, StateGraph execution, agent teams and resource routing.

**Memory** owns persistent experience, semantic context, engineering episodes and evidence lineage.

**Learning** owns experimentation, causal attribution, capability invention and curriculum evolution.

**Arena** owns independent evaluation, sealed holdouts, transfer evaluation and external attestation.

**Guard** owns security, authority, verification, canary and rollback.

## Pre-implementation review

Before graph execution, `Orchestrator.run` records a structured review from the request context: requirements, assumptions, acceptance criteria, constraints, non-goals, and optional planner/reviewer findings. `EngineeringDesignGuard.review_request` identifies common explicit conflicts and weak/vague success claims, creates a digest-bound receipt, and records findings in the run evidence and trajectory. Unresolved explicit contradictions stop graph execution before side effects; a documented resolution is retained in the receipt. The check is deliberately bounded: heuristic rules do not prove semantic consistency, and reviewer-supplied findings remain necessary for deeper ambiguity and domain-specific conflicts. This review extends the canonical design gate rather than replacing authority, verification, or promotion gates.

## Authority model

```text
Core → Engine → external action
          ↓
       Memory
          ↓
       Learning
          ↓
        Arena
          ↓
        Guard
          └── authorize / reject / canary / rollback
```

Learning may propose change. Arena may establish independent evidence. **Only Guard-authorized contracts may cause consequential lifecycle mutation.**

## Execution-boundary adoption status

The execution authority contract now has opt-in adapters at the governed sandbox, AI coding agency, and specialist-team spawn boundaries. Authority-bound team execution requires a run/task-matched binding and task-owned budget guard for every work unit, explicit repository paths, and host-supplied repository revision/digest. These checks run before the worker is invoked and are covered by regression tests.

The compatibility path remains available where callers do not provide bindings, so this is not a claim that every consequential call site is universally bound. Hosts must derive repository identity from a trusted checkout, enforce path scope at actual filesystem/process boundaries, persist checkpoints in trusted storage, and retain downstream idempotency for irreversible effects. Repository CI does not establish OS isolation or independently operated Arena evidence.

## Architectural invariants

1. AUREN is the sole current product/runtime identity.
2. Every capability has one primary domain owner.
3. Domains communicate through explicit contracts and evidence.
4. Learning cannot authorize.
5. Evaluation cannot authorize.
6. Memory does not convert observations into facts by itself.
7. Arena evidence remains independent of the capability being evaluated.
8. Guard controls consequential execution, promotion and rollback.
9. Missing, stale, contradictory, contaminated or unauthenticated evidence fails closed.
10. New capabilities use the AUREN domain vocabulary and must declare ownership.

The six domains are ownership and trust boundaries, not a requirement to split the runtime into six heavyweight processes. AUREN remains portable and dependency-light.
