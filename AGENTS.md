# Repository AI Agent Instructions

This repository uses an adaptive, provider-neutral AI coding orchestrator. These instructions apply to Claude Code, Codex, Gemini, local agents, and other compatible AI coding tools.

Canonical deployable skill: `skills/ai-coding-orchestrator/SKILL.md`.
Generic runtime skill: `.agents/skills/ai-coding-orchestrator/SKILL.md`.
Claude plugin manifest: `.claude-plugin/plugin.json`.
Core orchestration contract: `.ai-harness/ORCHESTRATION_SPEC.md`.
Knowledge fabric: `.ai-harness/KNOWLEDGE_FABRIC_POLICY.md`.
Canonical memory routing: `portable/memory_orchestrator.py`; ownership rules: `docs/CAPABILITY_MEMORY_OWNERSHIP.md`.
Engineering design policy: `.ai-harness/ENGINEERING_DESIGN_POLICY.md`.
Root engineering principles contract: `ENGINEERING_PRINCIPLES.md` (mandatory task-time applicability and evidence rules).
AI-era principle registry: `portable/ai_era_principles.py`; canonical catalogue: `docs/AI_ERA_FOUNDATION_PRINCIPLES.md`.
Deterministic design gate: `portable/engineering_design_guard.py`.
Optional extension registry: `.ai-harness/extension_registry.toml`.
Deployment guide: `docs/DEPLOYMENT.md`.

## Default workflow

For every non-trivial software-engineering task:

1. Understand the request, source, Jira or issue context, constraints, acceptance criteria, non-goals, and stopping condition. Before implementation, challenge the brief for blind spots, weak assumptions, missing success measures, and contradictory requirements. Offer a constructive question and options with trade-offs; do not silently resolve user-owned decisions.
2. Inspect repository structure, instructions, dependencies, tests, nearby implementations, and current git state before changing code.
3. Before creating new code, search for reuse candidates: existing implementations, extension points, interfaces, helpers, clients, repositories, query paths, configuration, tests, logging, and exception types serving the same responsibility.
4. Run the repository convention profiler when introducing or changing infrastructure or when the repository pattern is unclear.
5. Inspect available code intelligence before broad file exploration. Prefer repository AST/symbol indexes, Graphify, codebase-memory-mcp, and other already-installed structural sources when available; fall back cleanly when they are not.
6. Detect relevant optional process/context skills such as Superpowers, Ponytail, Caveman, and other Agent Skills when available. Use their best applicable capability without making them mandatory or duplicating their full workflow.
7. Infer the minimum safe capabilities needed. Research, POC, Debug, Grill, Review, Validation, and Learning are selected automatically when useful.
8. Select the smallest safe model, tool, context, and reasoning budget and escalate only when risk, uncertainty, scope, or failed verification warrants it.
9. Before non-trivial implementation, call `AdaptiveRuntime.recommend_design(...)` / `EngineeringDesignGuard.recommend_approaches(..., risk=...)` using the actual repository root, task intent, changed paths and risk. Inspect manifest/version evidence, reuse candidates, alternatives, trade-offs, verification and rollback suggestions. Then select relevant design dimensions using `.ai-harness/ENGINEERING_DESIGN_POLICY.md` and `EngineeringDesignGuard` to capture the contracts that matter: complexity, architecture, domain, data, resilience, security/privacy, performance/resources, concurrency, distributed consistency (CAP/PACELC), correctness, operations/recovery, AI-agent authority/evaluation, refactoring, legacy, construction and compatibility, including reuse, data-access, logging, exception, regression and exact-final-revision evidence when relevant. Recommendations are advisory: do not invent an unobserved framework/version or bypass tests, authorization, review or promotion gates. Record semantic contradictions/blind spots that deterministic checks cannot see; unresolved explicit conflicts stop implementation while independent, safe discovery may continue.
10. Implement using the repository's established architecture, naming, coding style, segregation, exception handling, logging, telemetry, dependency, and testing patterns. Prefer reuse or narrow adaptation over duplicate implementations.
11. Preserve existing usage pattern unless the request explicitly requires a behavior or contract change. This includes public APIs, callers, command flows, configuration semantics, persisted contracts, lifecycle ordering, and user-visible workflows.
12. Verify using repository-native evidence, inspect the final diff, and use independent review for meaningful or high-risk changes. Confirm DB/query calls are not unnecessarily multiplied, performance-sensitive paths remain within expected behavior, exceptions are not swallowed, logging follows repository convention, and regression coverage protects affected existing behavior.
13. Learn only from evidence-backed outcomes. Never silently rewrite harness code, security policy, provider permissions, or permanent engineering rules.

The normal runtime is a single adaptive run. Recursive or repeated execution is never automatic; it requires an explicit user request.

## Multi-Jira and Git branch safety

For multiple Jira work items in one repository, use `portable.multi_jira_workflow.BranchAwareWorkspace` and `ReadOnceFileCache`. Keep a distinct intent digest, acceptance criteria, task branch, test evidence, and PR per Jira. Snapshot branch/HEAD/upstream/status once; use the repository map and targeted symbol/test reads before full files; reuse unchanged file contents and invalidate after checkout/merge or edits. Reuse prior verified engineering lessons through `portable.memory_orchestrator.MemoryOrchestrator`, the canonical memory/provenance owner.

Create each task branch from the chosen feature branch, then compare remote SHAs and fetch the target only when it has changed. Merge latest target without rewriting shared history. Require a clean worktree before branch switching/syncing. On conflicts, preserve the conflict state and paths and stop for explicit resolution—never reset, clean, or silently choose a side. Stage explicit paths only; never use `git add .` or `git add -A`. Developer-local secrets, IDE state, virtual environments, local overrides, and machine-specific setup must not be staged. Shared templates such as `.env.example` may be tracked. Promote lessons only after verification with evidence IDs, the immutable intent digest, and explicit approval through canonical memory; never create a parallel Jira knowledge store. Exact-head checks, review, post-merge SHA verification, and rollback evidence remain mandatory.

### Delivery-mode and parallel-PR guardrails

- Resolve the effective workflow profile before repository actions and bind its version/digest to the durable task. The current explicit task instruction wins over saved preferences; repository/platform safety rules always remain binding.
- In `stage_only` mode, stage only task-owned paths. Never commit, push, open/create a PR, merge, publish, release, or deploy. Autonomous execution changes cadence, not delivery authority. Enforce this in the durable supervisor preflight before invoking a handler; generic handler names do not bypass the restriction. Keep unrelated and untracked user files untouched.
- In `working_tree` mode, do not commit or publish changes. In `commit_each_change` mode, commits are allowed only after the selected checks pass; push/PR/merge still need separate permission. In `pull_request` mode, preparing a PR does not authorize merge, release, publish, or deploy. Promotion actions still require operator-owned approval, exact-head required checks, current policy, and rollback evidence.
- Before creating or updating a PR, refresh the target ref and compare exact base/head SHAs. Detect shared-file ownership across all open PRs, including every paginated result. If a branch is behind the target or files overlap, serialize integration or split ownership before expensive CI/merge; never auto-pick a conflict side or rewrite shared history.
- The PR integration guard is an early warning and fail-closed merge gate, not a replacement for branch protection, required reviews, or required checks. Never report an overlapping/stale PR as mergeable just because GitHub's mergeability field is true.
- Do not auto-merge every open PR as a batch. Merge only when the effective delivery mode permits it, the PR is current and conflict-free, exact-head required checks and reviews pass, independent policy/approval gates pass, and post-merge verification plus rollback evidence are available. If mode is stage-only, stop at staged changes and report that no PR was opened or merged.



## Core foundation: grilling, evidence, and rework

Apply `skills/engineering/grilling-and-rework.md` as a core engineering policy, not an optional external skill. Before consequential or ambiguous changes, map the decision dependencies and acceptance criteria. Ask dependency-aware rounds: all currently answerable decisions together, never downstream questions whose prerequisites are open. Agents investigate facts and cite evidence; the user owns material product and trade-off decisions. Recommendations are not consent. Persist decisions and provenance in canonical task context; do not create parallel memory. Reopen only branches affected by changed answers. Unresolved decisions block only dependent tasks; continue independent safe work.

Implement in small verifiable slices, then challenge the result independently across correctness, completeness, security/privacy, compatibility, operations, tests, and rollback. When a criterion fails, rework the implementation rather than the review verdict, preserve the failure evidence, add or correct regression coverage, and re-run review and verification. Use `portable.engineering_recovery.EngineeringRecoveryLedger` to cap identical failure/hypothesis attempts at two and enforce total-attempt/time budgets. Switch approach or stop as blocked rather than looping. Require successful required checks on the exact current head; preserve the last verified state and report unverified work honestly. Arena/candidate review is advisory and never overrides user authority, Guard, or access controls.

## Actionable output is a core quality requirement

AUREN should produce outputs that enable a safe next action, not just analysis. Lead with outcome/state and prioritized steps. For each material action, give the concrete action, expected result or acceptance check, and evidence; include owner, dependency, timebox, effort, command, or path only when grounded and useful. Keep user-owned decisions separate from recommendations. Blockers must identify the missing condition, dependent tasks, independent work that can proceed, and the unblock step. Prefer concrete artifacts, runnable commands, file paths, tests, review findings, and rollback steps over vague advice. Substantial completion reports state status, verification evidence, remaining risks, and the next action. Do not invent owners/deadlines or create a parallel action/evidence store.

## Book-derived engineering guardrails

The repository adopts the useful parts of the 14-source engineering collection as one coherent policy, not as 14 competing instruction layers. Use the smallest relevant lens for the task.

- Reduce cognitive load and protect information hiding; every new abstraction must earn its boundary.
- Keep policy independent from volatile frameworks, persistence, transport, vendors, and deployment details.
- Prefer precise names, focused routines, explicit mutation, simple control flow, deliberate validation, and behavior-focused tests.
- Model business language, ownership, invariants, bounded contexts, and aggregate boundaries when domain complexity warrants it; keep simple CRUD simple.
- Select enterprise patterns from concrete forces and keep transaction, persistence, concurrency, presentation, and integration ownership explicit.
- For data-intensive work, declare source of truth, durability/visibility, consistency, idempotency, ordering, evolution, replay, and repair semantics; avoid N+1 and unnecessary database/remote round trips.
- Separate feature/bug/refactor intent. Refactoring is small, behavior-preserving, verifiable structural work.
- For legacy areas, characterize uncertain behavior and create the smallest useful seam before attempting broad cleanup.
- For production paths, make timeout, retry, overload, isolation, observability, resource cleanup, authorization, and rollback behavior explicit.
- Keep one authoritative owner for each system fact, automate repeatable checks, shorten feedback loops, and keep expensive decisions reversible when evidence is weak.
- Treat performance parity, repository-native logging, intentional exception handling, and regression safety as part of implementation quality rather than optional cleanup.

The deterministic guard is a contract check, not a substitute for review. Missing contracts produce warnings by default and block configured safety-critical dimensions on high-risk work. `not_applicable` must include a reason.

## Optional extension model

Extensions are capabilities, not dependencies. They are detected but never installed, upgraded, enabled, disabled, or mutated automatically.

- Graphify: AST/knowledge graph and relationship/impact evidence.
- code-mem / codebase-memory-mcp: persistent structural graph, semantic search, call tracing, impact analysis.
- Superpowers: process skills such as brainstorming, TDD, systematic debugging, planning, execution, and skill development.
- Ponytail: YAGNI/minimal-change discipline.
- Caveman: compact communication and token-efficient subagent output.
- Other Agent Skills/MCP servers: task-specific capabilities when explicitly configured.

Extension conflict precedence:

1. Repository/team instructions.
2. Security and permission boundaries.
3. Acceptance criteria.
4. Existing repository architecture and conventions.
5. Verification requirements.
6. AI Coding Orchestrator.
7. Optional extension guidance.
8. Model preference.

Use the narrowest useful capability from each extension. Never let a compression or minimalism extension remove required safety, correctness, acceptance criteria, error handling, or verification.

## Knowledge fabric and Code-RAG

The repository is treated as a structured knowledge system, not a bag of text.

Use this retrieval order:

1. Explicit repository/team instructions and task constraints.
2. Fresh AST/symbol/dependency structure.
3. Graph traversal for callers, callees, ownership, dependencies, flows, and blast radius.
4. Exact lexical search for identifiers, APIs, errors, routes, configuration keys, and logs.
5. Semantic retrieval when a local provider is configured.
6. Targeted source reads for final evidence.
7. Verification output as the authority for correctness.

Graphify and codebase-memory-mcp are optional local providers. Do not install, upgrade, or mutate either tool automatically. If present and enabled, use them as structural evidence providers. Preserve provenance and distinguish extracted relationships from inferred relationships. Treat stale or unavailable indexes as advisory and fall back to source inspection.

The harness supports hybrid lexical + semantic + graph ranking. Dense embeddings and cross-encoder reranking are optional extensions, not mandatory dependencies.

## Naming and coding-style precedence

When naming files, types, functions, variables, tests, modules, namespaces, packages, or configuration:

1. Follow explicit repository or team instructions.
2. Follow the dominant local convention for the same responsibility.
3. When multiple local patterns exist, choose the most mature pattern that is compatible, scalable, maintainable, testable, and already used for comparable code.
4. Only when no meaningful local convention exists, use a current, mature, widely adopted ecosystem convention appropriate to the language and repository type.
5. Record a deviation when a deliberate exception is necessary.

Do not mix naming styles merely for consistency with an external framework. Local consistency and compatibility take precedence.

## File placement and segregation

New interfaces, classes, constants, services, handlers, models, adapters, clients, configuration, and tests must be placed according to the current repository segregation.

Before creating a new file:

1. Find related sibling files.
2. Identify candidate directories or modules.
3. Compare candidates using responsibility, domain cohesion, dependency direction, namespace/module/package alignment, neighboring naming, test proximity, reuse, scalability, and compatibility.
4. Prefer the strongest existing location.
5. Prefer an existing cohesive directory over creating a new layer or folder.
6. Keep contracts close to their owning abstraction unless the repository has a dedicated contracts layer.
7. Keep constants close to the bounded context that owns them unless they are genuinely cross-cutting and the repository has a shared convention.
8. Keep tests in the established test structure and mirror production organization when that is the local pattern.
9. Do not create generic Common, Shared, Utils, Helpers, Misc, or similar locations merely for convenience.
10. Record the placement decision and rationale in run evidence.

Use the placement analyzer when candidate locations are ambiguous: `python .ai-harness/placement.py <new-file-names>`.

## Engineering principles

The root contract is `ENGINEERING_PRINCIPLES.md`. Apply it to every assigned task, not only coding tasks. At task intake, classify the work (research, design, code, data, security, test, operations, documentation, AI/agent, or release), identify the applicable principles, and record either a decision/evidence or `not_applicable: reason` for material dimensions. Do not mechanically apply every pattern; use the smallest set that protects correctness, safety, compatibility, and maintainability. If a principle conflicts with user requirements, repository contracts, or safety boundaries, follow the established conflict order and document the trade-off.

For implementation tasks, use the existing `portable.engineering_design_guard.EngineeringDesignGuard` and task/evidence lifecycle as the enforcement path; do not create a parallel principles engine or evidence store. A task is not complete until applicable acceptance criteria and checks are evidenced, the final diff is reviewed, and unresolved risks are reported. High-risk work must fail closed when required evidence is missing. Principles guide the design; tests, static checks, review, runtime evidence, and Guard/Arena policy determine whether the result is acceptable.

Apply these proportionally, not as rituals:

- DRY without premature abstraction
- YAGNI
- KISS
- Dependency Inversion / Dependency Injection where materially useful
- SOLID selectively
- Separation of Concerns
- High Cohesion / Low Coupling
- Composition over Inheritance
- Least Knowledge
- Fail Fast and Explicit Failure
- Single Source of Truth
- Least Surprise
- Make Invalid States Hard to Represent
- Compatibility by Default
- Behavior-Focused Testing
- Security by Default
- Failure Awareness
- Observability
- Reversibility
- Least Privilege
- Locality of Change
- Evidence Over Assumption

## Fresh repository and third-party dependencies

If the project has no established implementation pattern:

- prefer standard-library or platform-native capabilities first;
- use a mature, widely adopted framework only when a framework is warranted;
- do not add third-party dependencies silently;
- check `.ai-harness/DEPENDENCIES.md` before introducing one;
- document purpose, scope, version policy, operational/security impact, and alternatives considered;
- keep optional capabilities opt-in unless the task requires them.

Graphify, codebase-memory-mcp, Superpowers, Ponytail, and Caveman are external integrations, not runtime Python dependencies of this repository. They remain optional and externally owned.

## Isolation and safety

Use isolated Git worktrees for high-risk, critical, long-running, experimental, or parallel mutating work.

Never allow multiple mutating agents to edit the same files concurrently without an explicit merge strategy.

Research and Grill are read-only by contract. POCs are experimental. Production modifications happen only in controlled execution phases.

Never execute generated production migrations or untrusted generated code against a developer machine or production system without the repository's approved sandbox/approval mechanism.

## Verification

A model's statement that work is complete is not evidence.

Before completion, use appropriate repository-native verification such as acceptance criteria, focused tests, integration tests, build/type/lint checks, static analysis, contract checks, error/failure paths, security checks, performance checks, migration/compatibility checks, final git diff and whitespace validation, and independent review for meaningful or high-risk changes.

For code changes, the verification record must also cover the reuse decision, usage-pattern compatibility, data/DB call behavior when relevant, performance impact, local logging and exception conventions, and regression safety net. Prefer evidence from existing tests and tooling over newly invented one-off checks.

Every retry must add new evidence or materially change the approach.

## Context and token discipline

Use stable repository instructions and compact context first. Prefer targeted structural queries, repository maps, graph paths, relevant memory, current command output, and compact phase summaries over full repository dumps or full transcripts.

Apply the FlashAttention-inspired principle operationally: keep stable context small and reusable, tile task evidence, rank before inclusion, and minimize unnecessary memory movement. This is a context-engineering principle, not a dependency on the FlashAttention library.

Caveman-style compression may be used for communication when available, but never compress away source, commands, errors, acceptance criteria, security constraints, or verification evidence.

Carry conclusions, decisions, failures, open questions, checkpoints, and evidence. Do not carry irrelevant history.

## Self-improvement

Record evidence-backed observations, useful lessons, route quality, verification outcomes, review findings, retries, and available token/tool metrics.

Promote durable patterns only after repeated successful observations. Learned knowledge may improve routing and future context selection, but must not silently modify executable harness code, security policy, provider permissions, or permanent engineering rules.

## Collaboration, handoff, and shared memory

Treat runtime components and optional skills as one evidence-sharing system, not isolated personas.

Use the collaboration fabric at `.ai-harness/runtime/collaboration.py` for meaningful phase/component handoffs.

- Carry the immutable `intent_digest` through every handoff.
- Share evidence-backed facts, decisions, risks, trusted DO/DON'T guidance, and unresolved questions with provenance.
- Build lineage as a graph: evidence -> finding -> decision -> change -> verification -> outcome -> learning.
- Validate a handoff before consuming it; reject intent mismatch and scope drift.
- Keep memory task-scoped and bounded. Do not flood a receiver with unrelated history.
- Share useful lessons across components, but never let learned advice override repository instructions, acceptance criteria, security boundaries, or immutable guardrails.
- When handing over, state what is known, what is proven, what is inferred, what remains unknown, open risks, and the exact next action.
- Related regressions should feed the same lineage so future RCA and implementation can see the original decision, evidence, outcome, and later miss.

The collaboration model is a chain for execution and a graph for knowledge. Components may evolve independently, but their contracts must remain compatible through intent, provenance, evidence IDs, and bounded shared memory.

## Completion report

Report outcome, files changed, validation evidence, independent review evidence when used, knowledge sources and extensions actually used, repository conventions reused, reuse candidates inspected and why they were selected/rejected, placement decisions, dependency decisions, principles materially applied, assumptions, remaining risks, and checkpoint or next action when intentionally incomplete.


## Memory orchestration

For memory-aware tasks, reuse `portable.memory_orchestrator.MemoryOrchestrator`. Route durable episodes/semantics through canonical PersistentMemory and ContextGraph provenance; keep working/sensory state bounded and task-scoped; use the existing context cache; connect procedural, reflective and prospective operations to their canonical owners through explicit callbacks. Never create a parallel memory database, background poller, or duplicate task scheduler. Required context that cannot fit must fail closed rather than be truncated. Keep idle metrics local and model-free.

For Python unit tests, follow the repository's standard `unittest` style. Use the existing `# pragma: no cover` class-level exclusion for passive DTO/POCO-style dataclasses only; do not exclude behavior-bearing logic to inflate coverage.

## Unified memory, searchable history, and minimum-input handoffs

The contract in `docs/UNIFIED_AGENT_MEMORY_HISTORY_SECURITY.md` is mandatory for every agent and every stage. Use `portable/memory_orchestrator.py` as the single canonical memory/provenance route. Before work or branch/agent switching, recall and verify relevant prior decisions, accepted patterns, regressions, and failed attempts; search source, Git/PR/issue/CI history where available; and carry a revision-bound handoff envelope. After verification, record evidence-backed learning for other authorized agents. Security, scope isolation, approvals, exact-head checks, rollback, and source verification cannot be weakened by memory or learning. Ask for the minimum missing user input; do not replay all history or silently drop required safety context.
