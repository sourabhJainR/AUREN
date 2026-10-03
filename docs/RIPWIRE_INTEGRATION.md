# Repository Intelligence Design Lineage

AUREN incorporates selected engineering ideas observed in Red Hat's `ripwire` repository. This is a design adaptation, not a code dependency or code copy.

## Canonical ownership rule

AUREN has **one repository/code graph store**: `portable.agency_codebase_context.CodebaseIndex`.

`portable.repository_intelligence.RepositoryIntelligence` is the task-facing facade over that store. Retrieval, callers, callees, impact, affected-test discovery, change awareness and deterministic packing all consume the same `CodebaseIndex` snapshot.

`portable.repo_intelligence` is intentionally only a thin compatibility entrypoint for existing skills and commands. It imports and delegates to `RepositoryIntelligence`; it must not grow its own crawler, symbol table, graph, cache or snapshot store.

This rule also applies to future Graphify, Repowise, Aider-map or other code-map integrations: adopt useful algorithms or views, but route them through the existing canonical store rather than introducing another repository index.

## Adopted concepts

| Ripwire concept | AUREN adaptation | Why it matters |
| --- | --- | --- |
| Deterministic repository crawl | `CodebaseIndex.build()` sorts directories/files, ignores generated/build trees and records a stable snapshot | Identical repository state produces the same graph and digest |
| One shared graph for many questions | Search, callers, callees, impact, affected-test candidates, situational change context and task packing read the same `CodebaseIndex` | Prevents each agent feature from inventing its own repository model |
| Ranked structural context | Path, symbol, import and graph signals produce deterministic ranked files | Reduces broad context reads and improves first-pass localization |
| Shallow graph expansion | Default graph depth is 1 and expansion is bounded | Keeps graph retrieval useful without allowing context explosion |
| Token-priced answers | `token_budget` is enforced before evidence is emitted | Context cost becomes an explicit execution constraint |
| Detail ladder | Map first, then ranked files/windows, then full bodies only when selected | Avoids paying for repository-wide file reads |
| Terminal answers | A single repository answer contains evidence, graph edges, metrics and unknowns | Reduces follow-up grep/read loops |
| Honest incompleteness | Unknowns include budget omissions and graph stopping conditions | A missing edge is not presented as proof of absence |
| Affected-test discovery | Canonical graph reverse traversal plus conservative candidates | Gives the verifier a concrete starting set without claiming execution coverage |
| Snapshot identity | SHA-256 digest is derived from the canonical index | Evidence can be tied to a precise repository state |
| Compact machine output | `render_compact()` provides a stable, low-noise answer surface | Coding agents can consume structural evidence without verbose prose |

## Deliberately not adopted

- No ripwire runtime or library dependency.
- No copied ripwire source code.
- No embeddings or vector database as a second code-map store.
- No second AST/symbol/call-graph owner.
- No automatic claim that a call edge is semantically proven when static resolution is ambiguous.
- No automatic mutation or edit authority in the repository map.
- No deep graph expansion by default.
- No similarity-selected context as a substitute for structural evidence.

## Future integration contract

When another code-map implementation is evaluated:

1. First compare its constructs against `CodebaseIndex` and `RepositoryIntelligence`.
2. If the construct is already present, improve the canonical implementation rather than adding a copy.
3. If a new representation is useful, derive it from the canonical store and retain one snapshot/digest identity.
4. If an external engine is materially better for a narrow language or query, use it as an adapter/provider whose output is normalized into the canonical model.
5. Never let Graphify, Repowise, ripwire, Aider-map or another tool become a parallel source of repository truth.

```text
                     +---------------------------+
                     | CodebaseIndex             |
                     | canonical repository map  |
                     +-------------+-------------+
                                   |
          +------------------------+-------------------------+
          |                        |                         |
 RepositoryIntelligence      Context/Evidence          Future adapters
          |                        |                  Graphify/ripwire/etc.
   +------+------+                 |                         |
   |      |      |                 |                         |
 search callers impact/tests   bounded context        normalize -> same store
   |      |      |                 |                         |
   +------+------+-----------------+-------------------------+
                  |
          one snapshot / digest
```

## AUREN-specific lifecycle

```text
intent
  -> canonical CodebaseIndex snapshot
  -> ranked structural evidence
  -> bounded context
  -> plan / act
  -> verification
  -> review
  -> regression / learning
```

The map is an accelerator. Verification, policy, provider permissions, and release gates remain authoritative.

## Usage

The compatibility command remains stable for existing agents:

```bash
python -m portable.repo_intelligence . --for="Fix the authentication timeout regression" --token-budget=4000
python -m portable.repo_intelligence . --mode=callers --symbol="authenticate"
python -m portable.repo_intelligence . --mode=impact --symbol="authenticate" --graph-depth=1
python -m portable.repo_intelligence . --mode=tests --symbol="authenticate"
python -m portable.repo_intelligence . --mode=situ --base=HEAD
python -m portable.repo_intelligence . --mode=pack-task --for="Add retry handling to the payment client"
```

The implementation behind those commands is `portable.repository_intelligence.RepositoryIntelligence`, backed by the same `CodebaseIndex` used by the existing graph-aware context pipeline.
