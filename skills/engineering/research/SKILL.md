---
name: research
description: Conduct bounded engineering research using repository evidence, external sources when available, explicit unknowns and a reusable evidence package.
disable-model-invocation: true
---

# AUREN Research

Use this skill for unfamiliar technology, architecture, dependency, framework, or implementation research before coding.

## Research loop

`question -> repository map -> existing evidence -> targeted sources -> compare -> unknowns -> recommendation -> handoff`

1. State the research question and decision it must support.
2. Start with the canonical repository map and existing `ContextEvidence`.
3. Search existing docs, ADRs, policies, tests and implementation before external research.
4. Acquire only the smallest additional sources needed to resolve the question.
5. Separate facts, source claims, measurements and inference.
6. Record unresolved questions explicitly.
7. Package findings into the existing evidence/provenance flow for implementation or review.

## Repository-aware research

Use:

- `portable.repo_intelligence.RepositoryMap` for structural discovery;
- canonical `CodebaseIndex` for symbol/context retrieval;
- existing evidence and provenance identifiers for traceability;
- bounded context packing instead of whole-repository prompts.

Never create a research-specific repository index, vector store or memory database.

## External research

When external sources are needed, prefer primary documentation, source code, standards and reproducible measurements. Record source identity and the relevant claim. Treat search snippets and model recollection as discovery aids, not evidence.

## Decision package

Return:

- question and scope;
- evidence and sources;
- options considered;
- compatibility with current AUREN contracts;
- unknowns and risks;
- recommended next experiment or implementation slice;
- evidence/provenance identifiers for downstream work.

Research should reduce uncertainty. It must not silently become implementation authority.
