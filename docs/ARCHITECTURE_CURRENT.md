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
