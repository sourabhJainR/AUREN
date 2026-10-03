# Repository Construct Traceability — HLD / LLD

## 1. Objective

Ensure every substantial LLM coding workflow is grounded in actual constructs from the target repository. The workflow supplies the model with a generated construct index and explicit rules for resolving files, symbols, configuration/data keys, and SQL/database objects.

The feature is implemented inside the coding harness and is provider-neutral. It does not depend on a particular programming language parser or LLM provider.

## 2. Actual implementation components

| Responsibility | Actual construct |
|---|---|
| Repository profiling | `.ai-harness/project_profile.py::build_profile()` |
| Construct discovery | `.ai-harness/runtime/construct_index.py::build_index()` |
| Construct model | `.ai-harness/runtime/construct_index.py::Construct` |
| Stable construct identifier | `.ai-harness/runtime/construct_index.py::_id()` |
| Code construct scanning | `.ai-harness/runtime/construct_index.py::_scan_code()` |
| SQL object scanning | `.ai-harness/runtime/construct_index.py::_scan_sql()` |
| JSON/config scanning | `.ai-harness/runtime/construct_index.py::_scan_data()` |
| Prompt-size projection | `.ai-harness/runtime/construct_index.py::compact_index()` |
| Reference validation primitive | `.ai-harness/runtime/construct_index.py::validate_references()` |
| Prompt integration | `.ai-harness/run.py::optimized_build_prompt()` receives the enriched repository profile |
| Provider execution | `.ai-harness/provider.py::main()` |
| Workflow orchestration | `.ai-harness/engine.py::run_task()` |
| Policy | `.ai-harness/CONSTRUCT_TRACEABILITY.md` |
| Configuration | `.ai-harness/config.toml::[construct_traceability]` |

## 3. Runtime flow

```text
Target repository
    |
    v
project_profile.py::build_profile()
    |
    +--> existing language/naming/test/dependency profile
    |
    +--> construct_index.py::build_index(ROOT)
              |
              +--> _scan_code()
              +--> _scan_sql()
              +--> _scan_data()
              +--> Construct records with deterministic IDs
    |
    v
project_profile.py returns construct_traceability.index
    |
    v
run.py::optimized_build_prompt()
    |
    +--> engine.build_prompt(... profile ...)
    |
    +--> task intent / capability / knowledge / IO-aware context
    |
    +--> model receives exact construct references and rules
    |
    v
provider.py -> selected provider CLI
    |
    v
research / plan / HLD / LLD / implementation / validation output
```

## 4. Construct identity

The primary human-readable reference is:

`path:line::name`

The stable machine-readable reference is:

`[rc-xxxxxxxxxxxx]`

The ID is derived from:

`kind + repository-relative path + construct name + source line`

This makes the ID deterministic for a given source snapshot while keeping the source path and symbol visible to the LLM and reviewer.

## 5. Supported constructs

### Source code

The dependency-free scanner recognizes common forms of:

- class
- interface
- record
- struct
- enum
- function/method

Languages currently covered by the lightweight scanner include Python, C#, Java, Go, Rust, TypeScript, JavaScript, Kotlin, Swift, Ruby, PHP, C and C++ source extensions.

### SQL

The scanner recognizes actual declarations for:

- stored procedures
- views
- tables
- database functions

No database object is fabricated when no SQL source exists.

### Data/configuration

The scanner exposes top-level JSON properties and YAML/YML/TOML keys as concrete references.

## 6. HLD artifact requirements

A generated HLD must identify the existing repository boundary for every component it describes. A component that does not exist before implementation must be explicitly labeled `NEW CONSTRUCT`.

Example:

```text
Existing:
  .ai-harness/runtime/agent_turn.py::AgentTurnStateMachine
  .ai-harness/provider.py::interrupt_provider

New:
  NEW CONSTRUCT: .ai-harness/runtime/construct_index.py::Construct
```

An HLD must not present an inferred component name as though it already exists.

## 7. LLD artifact requirements

A generated LLD should descend from component to exact symbol and execution boundary:

```text
File
  -> Type
     -> Method/function
        -> caller/callee
           -> data/config key
              -> test/validation command
```

For database-backed work:

```text
application method
  -> repository/client method
     -> query/SP/view
        -> table/view
           -> result shape
```

If any link cannot be established from repository evidence, the LLD must mark it `UNRESOLVED CONSTRUCT` rather than guess.

## 8. Research and POC requirements

Research reports must separate:

- repository facts
- external facts
- inference
- hypothesis
- unresolved evidence
- recommendation

Repository facts must carry actual repository references. External research must identify the external source separately. A POC must identify the exact existing constructs it integrates with and every new construct it introduces.

## 9. Verification requirements

Verification should map:

`test/command -> target construct -> observed result`

Examples:

```text
python -m pytest -q
  -> .ai-harness/tests/test_construct_index.py::test_known_reference_resolves
  -> PASS
```

or:

```text
dotnet test
  -> src/OrderService.cs::OrderService.CreateOrderAsync
  -> PASS
```

## 10. Evidence boundary

The construct index is a navigation and grounding aid. It is not a substitute for reading source code. For material changes, the model must inspect the referenced implementation and relevant callers/dependencies before modifying it.

The scanner is intentionally dependency-free. Parser upgrades can later add richer language semantics without changing the external reference contract.

## 11. Generated artifacts

The canonical machine-readable index is regenerated from the current repository by `.ai-harness/project_profile.py::build_profile()` and exposed to the run context. It is intentionally not persisted into the target repository as a generated source artifact, avoiding repository churn.

Run-level artifacts may reference the construct IDs and paths in prompts, outputs, telemetry and future traceability reports.

## 12. Failure handling

| Condition | Required behavior |
|---|---|
| Construct resolves | Use `EXISTING CONSTRUCT` reference |
| New design element | Use `NEW CONSTRUCT` |
| Reference not found | Use `UNRESOLVED CONSTRUCT` |
| SQL absent | State that no SQL/database path was found |
| Index parser misses construct | Read source directly and record the evidence gap |
| Index generation fails | Do not fabricate repository constructs; fall back to normal repository inspection with an explicit degraded-grounding state |

## 13. Scope

This capability is part of `Hello_world_With_Structure` only. It is not an AUREN integration and must not introduce AUREN dependencies, terminology, identifiers, or artifacts.