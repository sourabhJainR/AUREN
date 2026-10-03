---
name: retro
description: Retrospect on an engineering session and turn observed failure patterns into bounded, verifiable improvements to the AUREN environment.
disable-model-invocation: true
---

# AUREN Retrospective

Use this skill when the user asks for a retrospective or asks how the engineering environment should improve after a session.

The goal is not to rewrite process prose. The goal is to find the smallest durable improvement that prevents a demonstrated failure from recurring.

## Evidence first

Read the session's available evidence before proposing changes:

1. `Engineering State Ledger` entries and receipts.
2. `ContextEvidence`, repository snapshot digest, selected paths and graph evidence.
3. tool/action history, verification results, failed checks and retries.
4. relevant CI workflow and repository-native check commands.
5. existing policies, skills and docs before creating anything new.

If the session evidence is unavailable, say so and perform only a repository-state retro. Never invent a session history.

## Find candidates

Classify observed improvement candidates into:

- **Navigation**: the agent could not find the right source, owner, symbol or dependency quickly enough. Prefer repository-map pointers, context indexes or documented entrypoints.
- **Automated checks**: a deterministic failure could have been caught by an existing or inexpensive lint, typecheck, test, contract or CI guardrail. Reuse existing checks before adding another one.
- **Coding standards**: classify first. Mechanical rules belong in executable checks. Judgement calls belong in review guidance. Do not turn deterministic rules into prose-only policy.
- **Context access**: important information was unavailable or repeatedly reacquired. Improve the canonical context/evidence pipeline rather than adding another memory or repository store.
- **Tool economy**: expensive or redundant calls consumed context or time. Prefer bounded retrieval, cached canonical evidence and the repository map.
- **No-ops**: instructions that do not change behavior. Remove them instead of making the steering files larger.
- **Verification gaps**: generation and verification were coupled, a check was skipped, or a result was accepted without a receipt. Strengthen the existing verification gate.
- **Lifecycle gaps**: research, plan, implementation, review, rollout or observation lost lineage. Extend the existing state ledger rather than adding another lifecycle model.

## Prioritize

Rank candidates by:

`observed failure -> recurrence likelihood -> blast radius -> prevention cost`

Prefer one or two high-value changes over a large process rewrite.

## Implementation rules

Before changing anything:

- check whether the capability already exists under another name;
- use `RepositoryMap` / canonical `CodebaseIndex` for navigation;
- reuse `ContextEvidence`, provenance and verification contracts;
- do not introduce a second repository graph, memory store, evidence ledger or task-planning abstraction;
- preserve provider/model neutrality and explicit safety boundaries.

For a mechanical finding, implement the deterministic guard when practical and add a regression test. For a judgement finding, add the smallest review/policy rule and a concrete example.

## Output contract

Produce:

- evidence observed;
- candidate findings with category and severity;
- selected improvement and why it prevents recurrence;
- exact files/contracts/tests to change;
- verification receipt;
- remaining uncertainty.

A retro is complete only when the proposed improvement is either implemented and verified or explicitly recorded as an actionable finding. Do not claim that a process improved merely because a recommendation was written.
