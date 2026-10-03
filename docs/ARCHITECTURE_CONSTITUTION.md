# AUREN Architecture Constitution

## Purpose

This document explains the machine-readable architecture authority at [`architecture/architecture.yaml`](../architecture/architecture.yaml). The YAML-compatible JSON contract is the source of truth for canonical ownership and dependency direction. This document explains the intent; it does not create a second architecture registry.

## Canonical engineering spine

```text
intent
  -> contract
  -> repo_facts
  -> decisions
  -> evidence
  -> plan
  -> execute
  -> verify
  -> review
  -> regression
  -> release
  -> outcome
  -> learn
```

The sequence deliberately separates evidence from execution state and keeps learning at the end of the control loop. Learning may recommend a strategy, but it cannot authorize an action or weaken a gate.

## Ownership rules

| Domain | Canonical owner | What it owns |
|---|---|---|
| Repository model | `portable.agency_codebase_context.CodebaseIndex` | Structural and semantic repository truth |
| Context/evidence | `state/engineering-state.schema.json:evidence` | Evidence identity, source, claim, confidence, freshness and provenance contract |
| Planning | `portable.task_planner.TaskPlan` | Dependency-aware work planning and readiness |
| Execution | `portable.agency_state_graph.StateGraph` | Bounded graph execution semantics |
| Capabilities | `portable.agent_capabilities.CapabilityFabric` | Capability meaning, risk and provider resolution |
| Verification | `state/engineering-state.schema.json:verification` | Verification result contract and evidence references |
| Release | `docs/REGRESSION_CANARY.md` | Regression, shadow, canary, promotion and rollback gates |
| Learning | `portable.agency_adaptive_planning` | Advisory observations and strategy recommendations |
| Architecture views | `skills/engineering/interactive-documentation` | Evidence-backed visualization only |

The important rule is not the table itself. It is the one-owner constraint behind it: compatibility APIs, agent teams, documentation renderers and adapters must consume these owners rather than silently establishing parallel truth.

## Compatibility policy

Historical and compatibility surfaces are allowed because they protect users and integrations. They are not allowed to become permanent competing implementations.

Every compatibility surface should eventually have four facts available:

1. status (`adapter`, `strategy`, `view`, `deprecated`, or `historical`);
2. canonical owner;
3. replacement path;
4. concrete removal condition.

This slice establishes the constitution and its enforcement mechanism. The next consolidation slices will progressively move runtime code to these boundaries and record the compatibility metadata where it currently exists only in prose.

## Portable boundary

`portable/` is the dependency-free runtime boundary. It may use the Python standard library and explicitly declared runtime dependencies, but it must not import from skills, harness surfaces, Claude/agent integration surfaces, or architecture documentation.

The architecture validator checks this direction with Python AST inspection so a new import cannot accidentally reverse the dependency flow.

## What is intentionally not changed yet

This first slice does not rename or remove the existing repository map, execution-plan, agent-team, lifecycle, or historical compatibility modules. Those changes are deliberately deferred until each replacement can be verified independently. The constitution therefore constrains future work without risking a broad architectural rewrite in one change.

The next slices are expected to:

- converge repository intelligence behind `CodebaseIndex`;
- introduce one execution envelope connecting intent, evidence, plan, state, changes, verification and outcome;
- strengthen evidence freshness and receipt lineage;
- make multi-agent execution an explicit strategy on top of the state graph;
- harden deterministic state serialization and effect-aware retries;
- add architecture coverage and compatibility removal tracking.

## Enforcement

Run locally:

```bash
python scripts/validate_architecture.py
python -m unittest tests.test_architecture_contract -v
```

Pull requests and pushes to `main` run the same checks through `.github/workflows/architecture-integrity.yml`.

The goal is simple: one obvious answer to where a piece of truth lives, while preserving the existing capabilities until each migration is proven safe.
