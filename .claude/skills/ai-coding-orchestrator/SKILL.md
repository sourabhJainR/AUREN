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

AUREN should leave users able to act. Lead with outcome, state and next steps. For each material action, give action, expected result/check and evidence; include owner, deadline, command or path only when grounded. For blockers, state the missing condition, affected dependencies, safe work and unblock step. Prefer runnable commands, tests, paths, rollback steps and artifacts. Finish substantial work with status, verification, risks and one next action. Keep routine output concise and consequential output decision-ready. Follow grilling/rework guidance; do not create a parallel action/evidence store.

## Mandatory principle selection at task intake

Before planning, classify task shape/risk and consult ENGINEERING_PRINCIPLES.md plus .ai-harness/ENGINEERING_DESIGN_POLICY.md. Select applicable principles and record evidence in canonical task context; use explicit not_applicable: reason rather than omit material dimensions. Adapt non-code work to accuracy, evidence, privacy, actionability and reversibility. For code, use EngineeringDesignGuard and existing gates. Policies never override acceptance, authority, security, permissions or repository architecture.

For high-risk changes, ensure the deterministic guard covers the relevant expanded dimensions: security/privacy, performance/resource budgets, concurrency/liveness, distributed consistency (CAP/PACELC), data correctness, operational recovery, and AI-agent authority/evaluation. State concrete trade-offs and testable expectations. The guard receipt belongs in canonical task evidence and must remain bound to the immutable intent digest. A blocked receipt cannot be bypassed by retries, a different agent, or a successful unrelated test; fix the missing contract, provide a reasoned `not_applicable: reason`, or stop as blocked. Re-run applicable verification on the exact final revision.

At completion, verify each selected principle through observable evidence: tests/checks, source references, diff/review, security and compatibility checks, or a reasoned documented decision where automated verification is not meaningful. Re-evaluate after fixes and against the exact final head. If evidence is missing, report it as missing; do not mark the task verified. Learning may improve future selection but cannot silently weaken permanent policy.

## Multi-Jira, branch-aware execution

For each Jira, create a distinct task context and branch, but reuse repository knowledge and verified engineering lessons. Use `portable.multi_jira_workflow.BranchAwareWorkspace` and `ReadOnceFileCache` rather than ad-hoc Git commands or repeated full-file reads.

- Capture Jira key, acceptance criteria, dependencies, non-goals, immutable intent digest, and exit conditions before implementation.
- Snapshot branch/HEAD/upstream/dirty and staged paths once; retrieve the repository map and targeted symbols/tests before opening full files. Reuse cached file contents while unchanged; invalidate affected paths after checkout, merge, generation, or detected edits.
- Recall prior verified project lessons through `portable.memory_orchestrator.MemoryOrchestrator`; repository files and current tests remain authoritative.
- Start each Jira branch from the selected feature branch, then conditionally fetch server refs and merge the latest target branch. Do not rewrite shared history. Stop on conflicts and preserve their paths for explicit resolution.
- Require a clean worktree before branch switching or syncing. Never hide local changes with reset/clean.
- Stage explicit source/test/doc paths only via `stage_paths([...])`; never use blanket `git add .` or `git add -A`. Developer-local secrets, IDE state, virtual environments and local overrides must never enter the index. Shared templates remain allowed.
- Keep work items independent unless dependency/file ownership proves coordination is needed. After changes from another Jira affect a dependency, invalidate affected context and rerun impact analysis/tests.
- Promote learning only after verification and with evidence IDs, intent digest, and explicit approval through the canonical memory owner. Do not create a Jira sidecar memory database or turn guesses into facts.
- Require exact-head CI and review before merge; record the final SHA, checks, artifacts, rollback notes, and unresolved external gates in the Jira/PR record.

See `docs/MULTI_JIRA_ENGINEERING_WORKFLOW.md` for lifecycle details and examples.

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
