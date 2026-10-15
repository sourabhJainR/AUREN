---
name: ai-coding-orchestrator
description: Repository-aware AI coding workflow for research, implementation, review, verification, visual documentation, and safe rollout.
---

# AI Coding Orchestrator

Use the repository as the source of truth. Keep work bounded, evidence-backed, deterministic where possible, compatible, and easy to verify. The orchestrator facilitates work; it does not create a second intelligence or ownership layer.

## Start here

For structural, cross-file, unfamiliar, or risk-sensitive tasks, start with the repository map, then retrieve only the context needed for the task. Prefer one capable agent first; add coordination only when independence, prior failure, measurable benchmark value, or a clear safety boundary justifies it.

```bash
python -m portable.repo_intelligence . --for="<task>" --token-budget=4000
```

Use focused map queries for callers, callees, impact, tests, and current situational state. Treat the map as evidence acceleration, not proof; retain its digest, confidence, skipped files, parse errors, and unknowns.

## Contract anchors

`portable.task_planner.TaskPlan`, `portable.impact_analysis`, `portable.repo_intelligence.RepositoryMap`, `CodebaseIndex`, `ContextEvidence` and `.ai-harness/runtime/tool_runner.py`, `.ai-harness/runtime/lsp_server.py`, `.ai-harness/runtime/feedback_loop.py`, `.ai-harness/runtime/auto_compaction.py` remain canonical. `downgrade=explicit_install_only` applies to artifact installation.

The `Engineering State Ledger` is the canonical lifecycle spine. For repeated hard failures, `portable.engineering_recovery.EngineeringRecoveryLedger` owns durable attempt budgets and checkpoints; `skills/engineering/arena-recovery.md` defines the Arena-informed candidate/attack/defend/review workflow. Control-plane policies are: `ORCHESTRATION_SPEC.md`, `TEN_LOOP_POLICY.md`, `CONTEXT_POLICY.md`, `ARCHITECTURE_POLICY.md`, `EXECUTION_POLICY.md`, `VERIFICATION_POLICY.md`, `REVIEW_POLICY.md`, `LEARNING_POLICY.md`, `TOKEN_POLICY.md`, `PROVIDER_CONTRACT.md`, `QUALITY_GOVERNANCE.md`.

## Core grilling and rework foundation

Read and apply `skills/engineering/grilling-and-rework.md` for consequential or ambiguous work. Treat dependency-aware grilling as decision discovery, not a questionnaire ritual: agents establish facts with evidence; users own material decisions; ask the whole currently-unblocked question frontier in rounds; defer dependent questions; and record resolved choices in canonical task/decision state. A changed answer reopens only affected branches. Unresolved choices block only dependent tasks, while independent safe work continues.

For implementation, use small verifiable slices and independent adversarial review. When a criterion fails, rework the implementation—not the verdict—and rerun regression tests and review. Same failure/hypothesis gets at most two attempts before an alternate approach or blocked status. Persist checkpoints, use exact-head CI, preserve the last verified state, and never turn a recommendation into user consent or a review into authorization.


## Actionable output is part of completion

AUREN should leave the user or next component able to act, not merely informed. Lead with outcome/current state and prioritized next steps. Each material action should state the concrete action, expected result or acceptance check, and supporting evidence. Include owner, dependency, deadline, effort, command, or path only when grounded and useful. Separate recommendations from user-owned decisions. For blockers, name the missing condition, affected dependencies, safe work that can continue, and the exact unblock step. Prefer runnable commands, paths, tests, findings, rollback steps, and usable artifacts over generic advice. Finish substantial work with status, verification evidence, residual risks, and one next action. Keep routine output concise and consequential output decision-ready. Follow `skills/engineering/grilling-and-rework.md`; do not create a parallel action/evidence store.

## Mandatory principle selection at task intake

Before planning any assigned task, classify its shape and risk, then consult the root `ENGINEERING_PRINCIPLES.md` and `.ai-harness/ENGINEERING_DESIGN_POLICY.md`. Use the extended reference inventory in `ENGINEERING_PRINCIPLES.md` as the full catalogue, and select only the applicable items. Select only the principles relevant to the task and capture the decisions/evidence in canonical task context. Use explicit `not_applicable: reason` for material dimensions that do not apply; do not silently skip them. For non-code work, adapt the checklist to accuracy, evidence, privacy, actionability, and reversibility rather than forcing coding rituals. For code changes, use `EngineeringDesignGuard` and the existing verification gates. No principle checklist can override acceptance criteria, user authority, security/permission boundaries, or repository architecture.

For high-risk changes, ensure the deterministic guard covers the relevant expanded dimensions: security/privacy, performance/resource budgets, concurrency/liveness, distributed consistency (CAP/PACELC), data correctness, operational recovery, and AI-agent authority/evaluation. State concrete trade-offs and testable expectations. The guard receipt belongs in canonical task evidence and must remain bound to the immutable intent digest. A blocked receipt cannot be bypassed by retries, a different agent, or a successful unrelated test; fix the missing contract, provide a reasoned `not_applicable: reason`, or stop as blocked. Re-run applicable verification on the exact final revision.

At completion, verify each selected principle through observable evidence: tests/checks, source references, diff/review, security and compatibility checks, or a reasoned documented decision where automated verification is not meaningful. Re-evaluate after fixes and against the exact final head. If evidence is missing, report it as missing; do not mark the task verified. Learning may improve future selection but cannot silently weaken permanent policy.

## Required workflow

`intent -> context -> plan -> evidence -> change -> verification -> review -> artifact -> rollout -> observation`

For code changes, make reuse, compatibility, data/DB access, performance, logging/telemetry, exception handling, and regression evidence explicit. Prefer existing implementations and established contracts over duplication.

Read the detailed guidance before substantial implementation:

- [Retrieval and context](references/01-retrieval-context.md)
- [Engineering quality and reuse](references/02-engineering-quality.md)
- [Runtime contracts and chat trigger](references/03-runtime-contracts.md)
- [Evidence, review, rollout, and safety](references/04-evidence-rollout.md)
- [Working sequences and output discipline](references/05-working-sequence.md)

## Rules that always apply

Prefer minimal safe changes. Do not create parallel repository indexes, memory stores, capability catalogs, evidence stores, workflow engines, logging abstractions, or privileged paths. Preserve existing API shapes, lifecycle ordering, persisted contracts, CLI/HTTP behavior, and user-visible workflows unless the requested change explicitly requires a contract change.

For bugs: `reproduce -> isolate -> identify owner -> minimal fix -> regression test -> verify -> review adjacent behavior`.

For high-risk work or repeated failures, record the exact failure signature and a falsifiable hypothesis before retrying. Permit at most two attempts for the same failure/hypothesis pair; then switch to a documented alternate hypothesis/implementation, independent adversarial review, smaller verified increment, or safe rollback. Enforce the per-run attempt and elapsed-time budgets with `EngineeringRecoveryLedger`; exhaustion must stop as `blocked` with evidence, not loop. Persist phase checkpoints and the exact head SHA so work can resume after restart. Do not accept queued/running, stale-head, failed, or skipped required CI as verification; use `verify_ci_gate` and require all mandatory checks on the exact PR head. Independent candidate review informs a choice but does not replace tests or grant authorization.

Require explicit approval for destructive, irreversible, production, financial, privacy-sensitive, or external-message actions. Never bypass security, permission, scope, or regression gates.

Skills are orchestration surfaces and `skills are orchestration surfaces` that reuse canonical repository, context, evidence, and provenance stores. `interactive-documentation` remains a composed skill using canonical evidence and provenance.


## Repository-native design advice
For non-trivial work, inspect target files, manifests/lockfiles, pinned language/framework versions, existing implementations and tests. Reuse the current owner. Recommend one best-fit algorithm/workflow/pattern and briefly compare credible alternatives on relevant correctness, complexity/performance, security, maintainability, compatibility and operational trade-offs. Use APIs/idioms verified for this repo's versions; flag unknowns instead of guessing. Include tests, failure/restart handling, migration and rollback. Keep trivial tasks lightweight. Record evidence in existing task context/EngineeringDesignGuard; advice is not proof or authorization.

## Quota recovery
On provider quota/rate-limit, persist task state/checkpoint, honor Retry-After/reset metadata, release the lease and sleep until due. Do not ping providers or call an LLM while idle. Reuse ExecutionController and PersistentSupervisorDaemon; bounded retries park permanent/unknown exhaustion for operator action. Reuse existing checkpoints/compactor; never truncate protected constraints/evidence. Revalidate leases/gates and avoid replaying committed side effects.
