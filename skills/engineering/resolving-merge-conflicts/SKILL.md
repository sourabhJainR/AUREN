---
name: resolving-merge-conflicts
description: Resolve an in-progress git merge or rebase conflict using repository evidence, preserving intent and verifying the resulting state.
disable-model-invocation: true
---

# AUREN Merge Conflict Resolution

Use this skill only when a merge or rebase is already in progress and conflicts must be resolved.

## Flow

`inspect state -> understand both intents -> map impact -> resolve minimally -> verify -> continue or abort`

1. Inspect `git status` and the conflict set before editing.
2. Read the base/current/incoming versions and nearby repository contracts.
3. Use the canonical repository map and impact analysis to identify affected callers, tests and interfaces.
4. Resolve by preserving the intended behavior of both sides where they are compatible; do not choose by textual proximity alone.
5. Avoid unrelated cleanup while conflict state exists.
6. Stage only resolved files and verify the index is conflict-free.
7. Run the repository-native checks required by the affected area before continuing the merge/rebase.
8. Record the resolution and verification receipt in the existing provenance flow.

## Safety

Do not silently discard one side. If intent cannot be established from evidence, stop at the ambiguity and ask for a decision. Do not rewrite history outside the active merge/rebase operation.

## AUREN integration

Use `RepositoryMap`, `CodebaseIndex`, `ContextEvidence`, impact analysis and existing verification gates. This skill owns conflict resolution procedure only; it does not create another merge state, evidence ledger or repository graph.
