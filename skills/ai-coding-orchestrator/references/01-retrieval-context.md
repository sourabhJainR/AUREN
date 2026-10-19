# Retrieval and context

## Repository map first

For structural, cross-file, unfamiliar, or risk-sensitive tasks, prefer the dependency-free repository map before broad reading:

```bash
python -m portable.repo_intelligence . --for="<task>" --token-budget=4000
```

Focused questions:

```bash
python -m portable.repo_intelligence . --mode=callers --symbol="<symbol>"
python -m portable.repo_intelligence . --mode=callees --symbol="<symbol>"
python -m portable.repo_intelligence . --mode=impact --symbol="<symbol>" --graph-depth=1
python -m portable.repo_intelligence . --mode=tests --symbol="<symbol>"
python -m portable.repo_intelligence . --mode=situ --base=HEAD
```

The map is evidence acceleration, not proof. Preserve its snapshot digest, confidence, skipped files, parse errors, and unknowns. Use the detail ladder: `map -> ranked files/symbols -> signatures/windows -> full bodies only for selected items`.

## Single-agent-first

Start with one capable agent: `one agent -> observe -> verify -> stop or continue`. Use multi-agent execution only when independent work, prior failure, measurable benchmark value, or a clear safety/ownership boundary justifies the coordination cost. Remain provider/model neutral.

## Engineering State Ledger

Preserve:

`intent -> context -> plan -> evidence -> change -> verification -> review -> artifact -> rollout -> observation`

Use canonical `RepositoryIntelligence`, `CodebaseIndex`, `SymbolLocator`, context planning, graph expansion, and immutable `ContextEvidence`. Do not create parallel repository indexes, memory stores, capability catalogs, evidence stores, or workflow engines.

## Multi-ticket reuse and file reads

For a multi-Jira session, capture one branch/HEAD/worktree snapshot and use `portable.multi_jira_workflow.ReadOnceFileCache` to reuse unchanged file contents. Cache only reduces duplicate I/O; it never makes stale content authoritative. Re-stat before reuse, invalidate changed paths after checkout/merge/generation, and refresh RepositoryIntelligence/CodebaseIndex when their snapshot SHA does not match HEAD. Retrieve previous verified project lessons through `portable.memory_orchestrator.MemoryOrchestrator`; do not create a second repository index or Jira learning store.



Prefer symbol-aware and semantic retrieval over whole-repository prompts. Use bounded deterministic packing, ignore files, secret filtering, deterministic ordering, file-size limits, and token budgets. Use `.ai-harness/runtime/feedback_loop.py` only when fresh evidence can change the next bounded action.
