# AUREN — Autonomous Unified Reasoning & Engineering Network

**Current release:** `1.0.5`  
**Canonical repository:** `sourabhJainR/AUREN`  
**Runtime state:** `~/.auren`

A provider-neutral AI engineering system that turns software work into a measurable, evidence-backed learning loop. Read [About AUREN](ABOUT.md) for the product overview and the [current architecture](docs/ARCHITECTURE_CURRENT.md) for canonical domain ownership and evidence boundaries. AUREN combines repository intelligence, task decomposition, capability routing, graph-based agent execution, bounded local/cloud resources, verification and review, durable evidence, regression replay, and continuous strategy learning. Its improvement loop is empirical rather than self-asserted: generate independent benchmarks, execute them through sealed boundaries, measure transfer on new domains, estimate causal effects, re-test on fresh holdouts, and promote or roll back learned capabilities only when the evidence gates pass.

AUREN separates **execution authority from learning**. Active runs operate under bounded, policy-gated execution; learning is recorded as evidence and can influence future runs only after independent evaluation, regression checks, and promotion/rollback decisions. The result is an engineering system designed to improve from observed outcomes while remaining auditable, reproducible, and fail-closed when evidence is insufficient.

## Project status (reviewed 2026-10-21)

**Verified implementation evidence snapshot:** main SHA [`c072838aeb28656aedd7730cdae8941312c9065b`](https://github.com/sourabhJainR/AUREN/commit/c072838aeb28656aedd7730cdae8941312c9065b) passed all six post-merge workflows after PR [#771](https://github.com/sourabhJainR/AUREN/pull/771) added bounded journal-lock waits. The subsequent documentation-only refresh has its own checks and deployment; follow [live Actions](https://github.com/sourabhJainR/AUREN/actions) for that revision. **Current release remains `1.0.5`**; these changes are not a versioned release.

PR [#764](https://github.com/sourabhJainR/AUREN/pull/764) merged the durable, append-only task progress journal, monotonic per-task event sequencing, cross-instance/process writer locking, heartbeat/staleness reporting, terminal-state handling, redaction, and concurrent-writer regression coverage. Five exact-head checks passed before merge: Runtime Resilience Matrix, Production AI Harness Hardening, AUREN Portable Bundle, Architecture Integrity, and AI Harness Check. Those results apply to the reviewed PR head; they are not independent external evaluation or a substitute for verifying workflows and deployment on the current main SHA.

**Progress integration in [PR #766](https://github.com/sourabhJainR/AUREN/pull/766):** `ExecutionController` now emits queued, claimed, preflight, handler, heartbeat, quota-wait, stale-recovery, and terminal events to the durable journal. The dashboard API exposes journal snapshots and the console renders stage, state, stale/unknown status, and next action. A terminal `completed` event is emitted only after the work-unit receipt commits; journal write failures surface through health findings without rewriting SQLite task state. PR [#770](https://github.com/sourabhJainR/AUREN/pull/770) adds a bounded-wait provider executor with lifecycle callbacks, a cap on orphaned workers, and a fail-closed timeout checkpoint. PR [#773](https://github.com/sourabhJainR/AUREN/pull/773) now applies the shared bounded-call helper to `ProviderBackedCrewAdapter` as well as the resumable executor, with a global eight-worker cap. It bounds caller wait but does not kill a timed-out thread; native transport deadlines and process isolation remain necessary. Remaining gates include provider-adapter wiring, cross-store crash reconciliation, cancellation/deadline/hard-termination fault injection, and external-effect reconciliation. Use `python auren_cli.py progress --json` to inspect the same durable journal from the command line.

**Open gates:** effective main-branch required-check enforcement; independent Arena evaluator and sealed-corpus custody; external sandbox/host-isolation audit; staging migration and rollback rehearsal; canonical provider lifecycle-event wiring into the progress journal; cross-store restart/fault-injection coverage; and task-scoped idempotency plus ambiguity reconciliation for consequential external effects. See the [alignment review](docs/REPOSITORY_WEBSITE_ALIGNMENT_REVIEW_2026-10-20.md), the [unified engineering completion roadmap](https://github.com/sourabhJainR/AUREN/issues/696), and the live [issues](https://github.com/sourabhJainR/AUREN/issues) for current owners and acceptance criteria.

For the responsive-execution contract, see [Responsive Execution and Timeout Policy](docs/RESPONSIVE_EXECUTION_AND_TIMEOUTS.md). For progress semantics and acceptance tests, see [Visible Incremental Progress](docs/VISIBLE_INCREMENTAL_PROGRESS.md). PR and issue state changes continuously; use the [live pull-request list](https://github.com/sourabhJainR/AUREN/pulls), [Actions](https://github.com/sourabhJainR/AUREN/actions), and [recent main commits](https://github.com/sourabhJainR/AUREN/commits/main) for live status.

## Inspect durable task progress

Read the supervisor's durable progress journal without starting or resuming work:

```bash
python auren_cli.py progress
python auren_cli.py progress --task-id <task-id> --json
python auren_cli.py progress --project-root /path/to/repository --stale-after-seconds 120
```

The command reads the project-local journal and the canonical executive journal, reports stale/unknown tasks explicitly, and returns a non-zero status for a missing task or unreadable journal. It is observational only; it does not imply that a task is running, successful, or safe to retry.

## Pre-implementation blind-spot review

Before graph execution, AUREN records a structured review of requirements, assumptions, acceptance criteria, constraints, non-goals, likely blind spots, and contradictory requirements. Common explicit conflicts can block implementation before side effects; a documented resolution stays in the receipt. Vague terms such as “fast”, “secure”, or “scalable” prompt measurable questions and flexible options rather than arbitrary hard stops. Semantic planner/reviewer findings supplement deterministic checks, which cannot guarantee complete contradiction detection.

The review should be constructive: explain why a concern matters, ask the smallest useful question, recommend an option with trade-offs, and offer narrower/reversible alternatives. User-owned decisions are not silently inferred; independent safe discovery may continue while dependent implementation waits. See [Engineering Design Policy](.ai-harness/ENGINEERING_DESIGN_POLICY.md) and [grilling and rework](skills/engineering/grilling-and-rework.md).

## AI-era foundation principles

AUREN now maintains a canonical, machine-readable catalogue of 20 AI-era engineering principles in [`portable/ai_era_principles.py`](portable/ai_era_principles.py), documented in [AI-era Foundation Principles](docs/AI_ERA_FOUNDATION_PRINCIPLES.md). They extend established practices such as SOLID, DRY, YAGNI, and KISS with controls for autonomous agents, tool authority, context integrity, independent evaluation, recovery, learning, and evidence-based promotion.

| Principle | Required behavior |
|---|---|
| IPACT — Intent Before Action | Set intent, constraints, acceptance criteria, and stopping conditions before work. |
| VERA — Verification Before Acceptance | Accept outcomes only with relevant verification evidence. |
| PACT — Permission And Control of Tools | Keep actions within granted authority and scope. |
| INVAR — Invariant Preservation | Preserve correctness, security, compatibility, and resource invariants. |
| TRACE — Traceable Results And Claims Engineering | Link important claims to sources, revisions, and evidence. |
| R3 — Recover, Resume, Roll Back | Make interruption and recovery safe and testable. |
| RAIL — Resource-bounded Autonomous Iteration Limits | Bound retries, time, cost, and resources; stop stalled loops. |
| CIG — Context Integrity Guard | Use relevant, current context and surface conflicts. |
| BLAST — Bounded Limits And Scoped Trust | Contain failures and limit blast radius. |
| INDEP — Independent Evaluation Protocol | Challenge results beyond the producer's own claims. |
| OWN — Ownership With Named Accountability | Assign clear ownership to tasks, transitions, and handoffs. |
| LEARN — Learning Evaluated Against Regression and Novelty | Learn from validated outcomes without weakening safeguards. |
| DELTA — Defensible Engineering with Limited, Targeted Alterations | Make the smallest justified change that meets the requirement. |
| HANDOFF — Handoff And Negotiated Definition Of Function | Specify delegation scope, inputs, outputs, acceptance, and failure behavior. |
| DEGRADE — Designed, Efficient, Adaptive Graceful Recovery Under Degraded Environments | Preserve safe core behavior or pause safely when dependencies fail. |
| TRUST — Treat Retrieved Untrusted Sources Thoughtfully | Separate untrusted content from instructions, permissions, and verified facts. |
| PROMOTE — Proof-Required Operational Maturation Of Tested Enhancements | Promote capabilities only after required evaluation and rollback checks. |
| ECONOMY — Engineering Cost Optimization Measured Over the Full Lifecycle, Not Yield Alone | Optimize full lifecycle cost and risk, not output speed alone. |
| MEASURE — Metrics, Evidence, And Results Evaluated | Measure outcomes, quality, recovery, and cost, not activity alone. |
| CONTEXT — Context Is an Engineering Dependency | Retrieve relevant, current, authoritative context before acting. |

### How this is integrated

- The existing `EngineeringDesignGuard` remains the canonical design-review gate; no parallel policy engine is introduced.
- The existing orchestrator records a deterministic receipt before graph execution, binding the principle registry to the task intent, changed paths, risk, and supplied code revision.
- Existing authority, verification, memory/context, recovery, independent-evaluation, and promotion/rollback gates remain responsible for enforcement.
- Focused regression tests cover registry completeness, owner/test traceability, receipt integrity, and recording before graph-node side effects.
- A receipt is traceability evidence, not proof that every control is fully enforced. External evaluator independence and operator-run production rollback still need their own evidence.

## Engineering-team pilot

For teams evaluating AUREN on a real repository, start with the [fixed-scope engineering reliability pilot](docs/ENGINEERING_PILOT_OFFER.md), the [outcome scorecard](docs/ENGINEERING_OUTCOME_SCORECARD.md), and the [security/data-flow worksheet](docs/SECURITY_DATA_FLOW_TEMPLATE.md). These are working templates, not claims of measured customer outcomes or completed security audits. The pilot should measure baseline versus assisted task lead time, review effort, regression outcomes, verification coverage, evidence completeness, intervention count and cost while preserving the customer's existing review and CI gates.

## Support AUREN

AUREN is an open-source project, and community support helps fund the time and resources needed to keep improving it. If you find the project useful or want to support its development, you can [buy me a coffee](https://buymeacoffee.com/sourabhjainr).

Contributions help support:

- **Development:** improve the runtime, agent orchestration, and engineering workflows.
- **Testing and reliability:** expand regression coverage and validate recovery and rollback paths.
- **Documentation and onboarding:** make AUREN easier to install, understand, and contribute to.
- **Independent evaluation:** work toward external benchmarks and validation, with results reported only when evidence is available.
- **Community contributions:** maintain examples, triage issues, and improve the contributor experience.

Support is voluntary and does not purchase influence over project decisions or guarantee specific features or outcomes. AUREN remains open to feedback, issues, and pull requests.

**Support AUREN:** https://buymeacoffee.com/sourabhjainr  
**Explore the project:** https://github.com/sourabhJainR/AUREN

## What AUREN is today

The primary product flow is designed to connect task intake through verification, learning, memory consolidation, empirical tuning, and controlled improvement. The repository also retains distinct compatibility and lower-level execution surfaces; their boundaries and outstanding integration gaps are documented in [`docs/REPOSITORY_CONSISTENCY_AND_ENHANCEMENT_PLAN.md`](docs/REPOSITORY_CONSISTENCY_AND_ENHANCEMENT_PLAN.md).

```text
Task / Intent
    |
    v
Context + Repository Intelligence
    |
    v
Cognitive Plan / Capability Routing
    |
    v
StateGraph / Agent Team
    |
    v
Verify -> Review -> Evidence
    |
    v
Record Experience
    |
    v
Continuous Maintenance Lane
    |
    +--> consolidate memory
    +--> replay / evaluate history
    +--> tune strategy + confidence
    +--> tune iteration target
    |
    v
Advisory policy for future tasks
```

The important architectural boundary is that **execution remains bounded and policy-gated** while learning is durable and continuous. The maintenance lane never mutates an active task's policy mid-run; learned changes are applied to future work after evidence and regression gates pass.

## Canonical ownership boundaries

| Area | Canonical owner |
|---|---|
| Dependency planning | `portable.task_planner.TaskPlan` |
| Default graph orchestration | `portable.orchestration.Graph` / `Orchestrator` |
| Supervisor-facing graph/team facade | `portable.state_graph.StateGraph` / `GraphAgentTeam` |
| Standalone checkpointable graph runtime | `portable.agency_state_graph.StateGraph` |
| Specialist proposal contract and routing | `portable.state_graph.GraphAgentTeam` + `portable.crew_adapters` |
| Consequential action authorization | `portable.action_governance.ActionGovernanceGateway` (host-configured adapter) |
| Optional isolated command execution | `portable.openshell_sandbox.OpenShellSandboxBackend` (explicit policy; no host fallback) |
| Governed isolated command execution | `portable.governed_sandbox_execution.execute_governed_sandbox` (policy verdict + sandbox evidence receipt) |
| Framework-neutral workflow contract and comparison metrics | `portable.orchestration_spec.OrchestrationSpec` / `compare_orchestration_trials` |
| Resource-aware harness team strategy | `.ai-harness/runtime/graph_agent_team.py` over `portable.agency_state_graph.StateGraph` |
| Capability semantics and routing | `portable.agent_capabilities` / `CapabilityFabric` |
| Durable memory | `portable.agent_capabilities.PersistentMemory` |
| Repository retrieval | `portable.agency_codebase_context.CodebaseIndex` and `portable.repo_intelligence.RepositoryMap` |
| Scheduling and run claims | `portable.automation_scheduler.AutomationScheduler` |
| Adaptive learning | `portable.adaptive_learning.AdaptiveLearningStore` |
| Empirical tuning | `portable.adaptive_tuning.AdaptiveTuner` |
| OS service lifecycle | `portable.maintenance_service` |
| Local bounded execution | `portable.local_offload.LocalOffloadBroker` |

Compatibility surfaces should adapt to these owners instead of maintaining independent state. The supervisor-facing `portable.state_graph` API wraps the existing `Graph`/`Orchestrator` path; `portable.agency_state_graph` is a distinct lower-level graph runtime. Keep their contracts explicit and add integration tests before assuming the two graph APIs are interchangeable. The harness graph now uses the shared crew-plan and proposal-validation helpers and dispatches host-registered adapters from a graph-owned node. No provider-specific live adapters are configured by default; consequential phases fail closed when required selected adapters are missing. The repository-level work tracked by issue #438 is now closed after durable supervisor-owned handoff recovery was implemented in [PR #658](https://github.com/sourabhJainR/AUREN/pull/658) and [PR #661](https://github.com/sourabhJainR/AUREN/pull/661), including restart/resume, idempotent replay, independent review, and aligned retry-budget digests. Provider-specific live adapters remain host-registered and are not configured by default. See [`docs/UNIFIED_GRAPH_CREW_RUNTIME.md`](docs/UNIFIED_GRAPH_CREW_RUNTIME.md) for the owner map and end-to-end invariants.

See [`docs/ACTION_GOVERNANCE_GATEWAY.md`](docs/ACTION_GOVERNANCE_GATEWAY.md) for the optional, fail-closed action-policy adapter and its security boundaries. It is not yet enabled by default across every production adapter. See [`docs/ORCHESTRATION_COMPARISON_PROTOCOL.md`](docs/ORCHESTRATION_COMPARISON_PROTOCOL.md) for the provider-neutral workflow contract and fair adapter benchmark metrics. See [`docs/OPENSHELL_SANDBOX_ADAPTER.md`](docs/OPENSHELL_SANDBOX_ADAPTER.md) for the optional OpenShell execution backend and its isolation limits. See [`docs/GOVERNED_OPENSHELL_EXECUTION.md`](docs/GOVERNED_OPENSHELL_EXECUTION.md) for the composed authorization-to-sandbox execution path and evidence receipt. See [`docs/CAPABILITY_MEMORY_OWNERSHIP.md`](docs/CAPABILITY_MEMORY_OWNERSHIP.md) for the capability/memory contract and [`docs/RIPWIRE_INTEGRATION.md`](docs/RIPWIRE_INTEGRATION.md) for repository-intelligence design lineage.

See [`docs/CAPABILITY_AWARE_EXECUTION.md`](docs/CAPABILITY_AWARE_EXECUTION.md) for capability-aware execution, optional installed-skill/MCP/plugin discovery, bounded selection, and graceful degradation.

## Durable autonomous execution

AUREN now supports company-level long-running work units through `portable.autonomous_company.AutonomousCompany`. The supervisor persists iterations, blocker decisions, alternate-path confirmation, CI waits, self-reviews, evidence, recipes, and a shareable trust score so work can resume after process restarts. `portable.executive_team.ExecutiveTeam` provides skill-specific agents under one durable task owner, while `RecipeMiner` extracts reusable patterns from the repository. See [`docs/AUTONOMOUS_EXECUTIVE_LOOP.md`](docs/AUTONOMOUS_EXECUTIVE_LOOP.md).

## Deterministic repository intelligence

AUREN builds one deterministic repository snapshot and reuses it for multiple structural questions.

```bash
python -m portable.repo_intelligence . --for="Fix the authentication timeout regression" --token-budget=4000
python -m portable.repo_intelligence . --mode=callers --symbol="authenticate"
python -m portable.repo_intelligence . --mode=impact --symbol="authenticate" --graph-depth=1
python -m portable.repo_intelligence . --mode=tests --symbol="authenticate"
python -m portable.repo_intelligence . --mode=situ --base=HEAD
python -m portable.repo_intelligence . --mode=pack-task --for="Add retry handling to the payment client"
```

The map provides deterministic file ordering and SHA-256 identity, symbol/import/call relationships, bounded graph expansion, explicit token budgets, affected-test candidates, parser-error disclosure, skipped-file disclosure, and explicit unknowns when evidence is incomplete.

Repository intelligence accelerates context discovery; verification, policy, review, regression, permissions, and release gates remain authoritative.

## Graph orchestration and agent teams

The intended composition is one task contract and evidence chain with explicit graph adapters, not parallel task engines. The default adaptive supervisor path uses `portable.orchestration.Graph/Orchestrator`; the resource-aware harness team uses the checkpointable `portable.agency_state_graph.StateGraph`. Their API boundary and the remaining crew-dispatch integration gap are documented in [`docs/UNIFIED_GRAPH_CREW_RUNTIME.md`](docs/UNIFIED_GRAPH_CREW_RUNTIME.md).

AUREN includes a dependency-free `StateGraph` runtime inspired by durable agent-graph patterns without taking a LangGraph runtime dependency. It provides state snapshots, reducers, conditional routing, bounded retries, checkpoints, interrupts, deterministic traces, bounded supersteps, and explicit parallel-safety controls.

`GraphAgentTeam` uses that graph runtime while retaining `TaskPlan` as the canonical dependency contract:

```text
TaskPlan
   |
   v
Dependency graph
   |
   +--> independent read-only agents -- bounded parallel supersteps
   |
   +--> mutating agents ------------ serialized execution
   |
   v
SharedTaskMemory
   |
   v
Verification / review / synthesis
```

Dependency failures are fail-closed. Read-only roles receive an explicit `patch_allowed: false` guard. The graph path is the default; `AUREN_GRAPH_TEAM=0` remains available for diagnostics and compatibility.

### Local resource offload

The graph/team layer can use an additive `LocalOffloadBroker` for independent local work that should not occupy the coordinator context. It detects CPU capacity, bounds concurrency, applies time/output budgets, scrubs secret-like environment variables, and can run jobs in temporary workspace copies. The broker is an execution resource, not a second orchestrator or policy owner.

```bash
python -m portable.local_offload --project-root . --workers 3 -- pytest -q
```

See [`docs/LOCAL_OFFLOAD.md`](docs/LOCAL_OFFLOAD.md) for the API and safety model.

## Engineering lifecycle and evidence

The twelve-phase engineering evolution control plane is available through `portable.engineering_evolution.EngineeringEvolutionControlPlane`, spanning canonical evidence, evidence graph, predictive feedback, AUREN compaction, impact/failure prediction, historical decomposition, provider calibration, cross-project validation, graduation, benchmark gates, and Ollama-independent embedded local LLM execution.

The canonical `EngineeringEvidenceEnvelope` carries immutable lifecycle references from context through changeset, verification, review, learning and release without duplicating evidence claims. See [`docs/CANONICAL_ENGINEERING_EVIDENCE_ENVELOPE.md`](docs/CANONICAL_ENGINEERING_EVIDENCE_ENVELOPE.md).

For substantial work the control plane follows one evidence lineage:

```text
research -> plan -> implement -> verify -> review -> shadow -> canary -> promote
                                                            |
                                                            +-> rollback
```

`ContextEvidence` is immutable. Verification creates a `VerificationReceipt`; review binds evidence to the same artifact and verification result. Shadow and canary require the appropriate prior receipts. Promotion requires matching evidence and can be rolled back.

The state-ledger flow is:

```text
INTENT
  -> CONTRACT
  -> REPO_FACTS
  -> DECISIONS
  -> EVIDENCE
  -> CHANGESET
  -> VERIFY
  -> OUTCOME
  -> OPEN_RISKS
  -> NEXT
```

## Continuous learning and self-improvement

Every completed task can become an experience record. The learning path is deliberately separated from the active worker path:

```text
experience
   -> deferred learning
   -> memory consolidation
   -> rolling benchmark history
   -> empirical comparison
   -> confidence calibration
   -> strategy / iteration tuning
   -> advisory policy
   -> future task
```

The current adaptive tuner maintains durable history and policy state. Promotion is bounded by observation counts, independent-task counts, confidence-adjustment limits, iteration floors/ceilings, and evidence requirements.

The maintenance lane is intentionally outside active orchestration. It claims one scheduled run, processes deferred learning, evaluates the longer history, writes a maintenance receipt, and releases the durable scheduler claim.

## Continuous maintenance service

`portable.maintenance_service` is the OS lifecycle wrapper for the existing maintenance lane. The scheduler remains the source of truth; the service does not create a second learning store or scheduler.

### Default schedule

The default maintenance schedule is the **last calendar day of every month at 02:00 local time**. The time, timezone, project root, polling interval, budget, enablement, and service scope are configurable.

Configuration defaults:

| Setting | Default |
|---|---|
| Schedule | Last day of every month |
| Time | `02:00` |
| Timezone | `local` |
| Poll interval | `60` seconds |
| Maintenance budget | `20` jobs |
| Service scope | `user` |
| Enabled | `true` |

The durable scheduler retains claim-before-run semantics and records run outcomes. Existing interval-based schedules remain compatible. The previously-created adaptive-learning five-minute schedule is migrated once to the monthly cadence instead of being deleted, preserving its run history.

### Foreground mode

Useful for development and validation:

```bash
python -m portable.maintenance_service --project-root /path/to/repo run
```

Run one maintenance attempt without entering a long-running loop:

```bash
python -m portable.maintenance_service --project-root /path/to/repo run-once
```

### Environment configuration

```bash
export AUREN_PROJECT_ROOT=/path/to/repo
export AUREN_MAINTENANCE_TIME=02:00
export AUREN_MAINTENANCE_TIMEZONE=Asia/Kolkata
export AUREN_MAINTENANCE_POLL_SECONDS=60
export AUREN_MAINTENANCE_BUDGET=20
export AUREN_MAINTENANCE_ENABLED=1
export AUREN_SERVICE_SCOPE=user
```

The scheduler itself stores the calendar policy in its durable task record, so service restarts do not reset the monthly schedule.

### Linux: systemd

Install the user service:

```bash
python -m portable.maintenance_service --project-root /path/to/repo --scope user install
```

Install as a system service when elevated service scope is required:

```bash
sudo python -m portable.maintenance_service --project-root /path/to/repo --scope system install
```

Manage it with:

```bash
python -m portable.maintenance_service --scope user status
python -m portable.maintenance_service --scope user start
python -m portable.maintenance_service --scope user stop
python -m portable.maintenance_service --scope user uninstall
```

The generated unit runs the existing Python maintenance host, restarts on failure, and keeps the calendar decision in AUREN's durable scheduler.

### macOS: launchd

Install the per-user launch agent:

```bash
python3 -m portable.maintenance_service --project-root /path/to/repo --scope user install
```

For a system daemon, use the `system` scope with the privileges required by the target machine:

```bash
sudo python3 -m portable.maintenance_service --project-root /path/to/repo --scope system install
```

Manage it with:

```bash
python3 -m portable.maintenance_service --scope user status
python3 -m portable.maintenance_service --scope user start
python3 -m portable.maintenance_service --scope user stop
python3 -m portable.maintenance_service --scope user uninstall
```

### Windows: Windows Service

Windows Service mode uses `pywin32` because a Python process must integrate with the Windows Service Control Manager rather than behave like a plain console process.

```powershell
python -m pip install pywin32
python -m portable.maintenance_service --project-root C:\path\to\repo --scope system install
```

Manage it with:

```powershell
python -m portable.maintenance_service --scope system status
python -m portable.maintenance_service --scope system start
python -m portable.maintenance_service --scope system stop
python -m portable.maintenance_service --scope system uninstall
```

The service starts automatically and waits efficiently for the durable monthly schedule rather than running a second scheduler.

### Service safety rules

The service only owns lifecycle and execution of the already-gated maintenance lane. It does not change credentials, permissions, merge authority, security policy, or active task policy. A failed maintenance cycle remains retryable through the scheduler's claim/run ledger instead of being silently discarded.

## Install, upgrade, rollback and uninstall

AUREN is installed outside the project repository under `~/.auren`. The release bundle is immutable and identified by version, source commit and bundle hash.

### Install the latest release

Linux/macOS:

~~~bash
curl -fsSL https://raw.githubusercontent.com/sourabhJainR/AUREN/main/install.sh | bash
~~~

Windows PowerShell:

~~~powershell
irm https://raw.githubusercontent.com/sourabhJainR/AUREN/main/install.ps1 | iex
~~~

### Install an exact release

~~~bash
AUREN_VERSION=v1.0.5 curl -fsSL https://raw.githubusercontent.com/sourabhJainR/AUREN/main/install.sh | bash
~~~

### Check for updates

~~~bash
python ~/.auren/current/auren_cli.py check-update
~~~

### Upgrade

~~~bash
python ~/.auren/current/auren_cli.py update
~~~

The updater verifies the remote source, builds a new immutable version directory, preserves existing state, and atomically switches `~/.auren/current`. It refuses automatic downgrades.

### Roll back

~~~bash
python ~/.auren/current/auren_cli.py rollback
~~~

Rollback selects the previous pinned installation. It does not mutate or overwrite immutable version directories.

### Uninstall

If the maintenance service is installed, remove the service first using the scope in which it was installed:

~~~bash
python ~/.auren/current/portable/maintenance_service.py --scope user uninstall
~~~

For a system-scoped service, use `--scope system` with the privileges required by the host.

Uninstalling the runtime and deleting persistent state are separate operations. Do not remove `~/.aer` or `~/.auren` as part of routine uninstall; legacy state may still be needed for recovery or migration. Before any intentional state deletion, confirm the exact path and make a verified backup.
AUREN does not install into or modify the target repository merely by being installed.

## AUREN Engineering Console

AUREN includes a lightweight local web UI for observing engineering execution, learning, evidence, regression protection, repository/code-graph state, quality signals, benchmarks, research activity, and test growth.

The console is observation-only and reuses AUREN's existing state owners. It is bundled into portable AUREN installations; no Node.js or separate UI installation is required.

After AUREN is installed, start it against the repository you want to inspect:

~~~bash
python ~/.auren/current/auren_cli.py dashboard --project-root /path/to/your/repository
~~~

Windows PowerShell:

~~~powershell
python "$HOME\\.auren\\current\\auren_cli.py" dashboard --project-root "C:\\path\\to\\your\\repository"
~~~

Then open http://127.0.0.1:8765.

If the default port is busy:

~~~bash
python ~/.auren/current/auren_cli.py dashboard --project-root /path/to/your/repository --port 8876
~~~

The dashboard uses the selected project root for repository intelligence and existing AUREN state. It does not expose source contents, prompts, credentials, or provide write/execution APIs.

See [dashboard/README.md](dashboard/README.md) for installation-path details, Windows/Linux/macOS commands, remote access through SSH tunneling, health checks, troubleshooting, and the direct module invocation.

## AUREN CLI and portable distribution

The GitHub Actions `auren-portable` artifact and the GitHub release asset are self-contained AUREN distributions. The release also includes one-command installers for Linux/macOS and Windows.

Install the latest published release on Linux/macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/sourabhJainR/AUREN/main/install.sh | bash
```

Install a specific release:

```bash
AUREN_VERSION=v1.0.5 curl -fsSL https://raw.githubusercontent.com/sourabhJainR/AUREN/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/sourabhJainR/AUREN/main/install.ps1 | iex
```

The installer downloads the release bundle, extracts the pinned launcher, verifies the bundle manifest and installs AUREN under user-scoped `~/.auren` storage. It does not modify the target repository.

Install directly from a downloaded release bundle:

```bash
python auren_cli.py auren-portable.zip
```

Build and verify a bundle locally:

```bash
git clone https://github.com/sourabhJainR/AUREN.git
cd AUREN
python auren_cli.py build --output auren-portable.zip
python auren_cli.py verify auren-portable.zip
```

AUREN records a provenance chain:

```text
semantic version -> exact source Git commit -> bundle SHA-256
```

The installed machine state is kept under:

```text
~/.auren/versions/v<version>/
~/.auren/current
~/.auren/current/install.json
~/.auren/active.json
~/.auren/automation/automation.db
~/.auren/memory/memory.db
```

Execution journals, telemetry, caches, worktrees, and Python caches remain outside the portable distribution.

## Repository isolation and safety

Installing, updating, or rolling back AUREN does not:

- add AUREN distribution files to the target repository;
- modify project source, tests, manifests, or configuration merely to install AUREN;
- modify Git remotes, hooks, branches, or ignore rules;
- silently modify MCP configuration, credentials, permissions, production access, or merge authority;
- allow learned behavior to weaken immutable safety or security controls.

When AUREN performs a user-requested engineering task, project changes are the requested engineering changes, not AUREN distribution artifacts.

## Capability roles

| Role | Typical use |
|---|---|
| Planner | Break down substantial implementation work |
| Explorer | Trace repository structure and dependencies |
| Researcher | Gather external/domain evidence when permitted |
| Builder | Implement requested code/configuration/test changes |
| Verifier | Run and interpret deterministic verification |
| Reviewer | Check correctness, compatibility, quality, and maintainability |
| Security reviewer | Examine elevated-risk changes and boundaries |
| RCA investigator | Diagnose without patching when investigation-only work is requested |
| Synthesizer | Combine evidence, decisions, unresolved risks, and next action |

Independent read-only work can be parallelized. Mutating agents are serialized behind declared dependencies.

## Repository map

```text
AUREN/
├── .ai-harness/                 # adaptive harness, policies, runtime and lifecycle
├── portable/                    # dependency-light distributable AUREN runtime
├── agency/                      # upstream agency assets and provenance
├── skills/                      # canonical local agent skills
├── .agents/                     # compatibility/agent skill surfaces
├── .claude/ + .claude-plugin/   # Claude skill and plugin packaging
├── docs/                        # architecture, lifecycle, research and deployment contracts
├── examples/                    # runnable examples and harness scenarios
├── scripts/                     # conformance, eval, packaging and validation tooling
├── state/                       # engineering state schema
└── tests/                       # portable and integration regression coverage
```

The repository keeps only active architecture, deployment, lifecycle, evaluation, and compatibility documentation. Historical implementation plans are not part of the product package.

## Reference documentation

- [Repository and Website Alignment Review (2026-10-20)](docs/REPOSITORY_WEBSITE_ALIGNMENT_REVIEW_2026-10-20.md) — synchronized status, evidence boundaries, and prioritized remaining gaps.

- [`docs/CAPABILITY_MEMORY_OWNERSHIP.md`](docs/CAPABILITY_MEMORY_OWNERSHIP.md) — capability, memory, and retrieval ownership.
- [`docs/RIPWIRE_INTEGRATION.md`](docs/RIPWIRE_INTEGRATION.md) — deterministic repository-intelligence lineage and boundaries.
- [`portable/LANGGRAPH_PATTERN_ALIGNMENT.md`](portable/LANGGRAPH_PATTERN_ALIGNMENT.md) — state-graph pattern mapping.
- [`portable/README.md`](portable/README.md) — portable distribution and machine-scoped lifecycle.
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — deployment and runtime integration.
- [`docs/USAGE_AND_PLATFORM_INTEGRATION.md`](docs/USAGE_AND_PLATFORM_INTEGRATION.md) — platform usage and integration.
- [`docs/ENGINEERING_WORK_REPORTS.md`](docs/ENGINEERING_WORK_REPORTS.md) — work-report and evidence flow.
- [`docs/REGRESSION_CANARY.md`](docs/REGRESSION_CANARY.md) — regression, shadow, and canary controls.

## Typical requests

### Bug fixing

```text
Fix the failing login test. Inspect repository instructions and the existing authentication flow first. Identify the root cause with evidence, make the smallest compatible change, add or update regression coverage, verify it, and report what changed and what was verified.
```

### Feature development

```text
Add retry handling to the outbound payment client. Preserve current API behavior, inspect existing retry and timeout patterns, implement the smallest safe change, add regression coverage, verify it, and report open risks.
```

### RCA

```text
Investigate why the nightly import occasionally drops records. Do not modify code. Trace the data flow and return facts, inferences, unknowns, root-cause confidence, and evidence.
```

### Code review

```text
Review this change for correctness, compatibility, security, regression risk, observability, and missing verification. Do not rewrite unrelated code.
```

## Design principle

**Use AI for engineering speed; use AUREN for engineering discipline.**

## Product Architecture

AUREN is organized as six canonical domains: **Core** (world/self model, reasoning, goals), **Engine** (planner, StateGraph, agents, resources), **Memory** (episodic, semantic, engineering, evidence graph), **Learning** (experimentation, causal attribution, capability invention, curriculum), **Arena** (independent evaluation, holdouts, transfer, attestation), and **Guard** (security, authority, verification, canary, rollback).

See [the canonical AUREN architecture](docs/ARCHITECTURE_CURRENT.md) for ownership and trust boundaries.
## GitHub Pages deployment

The public documentation site is served at `https://sourabhJainR.github.io/AUREN/`.

Keep these four entrypoints during repository cleanup:

- `site/index.html` and `site/404.html` are the canonical GitHub Actions deployment payload.
- `docs/index.html` and `docs/404.html` are retained as branch-publishing compatibility entrypoints because the repository previously used the `main/docs` Pages source.

Do not remove either pair unless the GitHub Pages repository setting has been verified to use GitHub Actions and the compatibility contract is intentionally retired. The Pages workflow validates all four files before deployment.


## Repository cleanup and rollback governance

Repository cleanup is governed by an evidence gate so that reducing repository size never silently removes AUREN's runtime, CI, release, compatibility, or public presence. The cleanup contract requires dependency inventory, classification, baseline capture, isolated PR review, regression evaluation, an actionable rollback path, and post-merge re-testing.

The protected-artifact manifest is [docs/CLEANUP_PROTECTED_ARTIFACTS.yml](docs/CLEANUP_PROTECTED_ARTIFACTS.yml), and the full procedure is [docs/REPOSITORY_CLEANUP_AND_ROLLBACK.md](docs/REPOSITORY_CLEANUP_AND_ROLLBACK.md).

**Rule:** if an artifact's dependency is uncertain, keep it until the dependency is disproven or a validated replacement exists. A green unit-test suite is not sufficient evidence for removing deployment, packaging, release, documentation, or externally referenced artifacts.


## End-to-end vibe coding

AUREN includes an evidence-gated, 18-stage vibe-coding lifecycle for product work: IDEA → RESEARCH → DEFINE THE USER → PRD → CHOOSE TECH STACK → ARCHITECTURE → DESIGN → PROJECT RULES → TASK BREAKDOWN → SETUP → DEVELOPMENT → TESTING → SECURITY REVIEW → CODE REVIEW → PREVIEW DEPLOYMENT → QA TESTING → PRODUCTION DEPLOY → MONITORING.

The lifecycle is an explicit dependency graph, not a prompt-only checklist. Every stage needs a handler and evidence-backed outputs; failed gates block dependent work. It supports durable per-stage checkpoints and restart/resume without replaying completed handlers. Production deployment requires an explicit approval envelope and a release receipt bound to the exact reviewed revision. Preview/production deployment may be marked not applicable only with an explicit rationale and evidence.

The runnable host entry point is `scripts/run_vibe_coding_project.py`; it discovers local Codex/Claude CLIs, connects configured MCP tools, runs in an isolated Git worktree, persists configuration-bound learning/checkpoints, and journals uncertain side effects. See [docs/VIBE_CODING_WORKFLOW.md](docs/VIBE_CODING_WORKFLOW.md) for the host config contract, real-project validation command, restart/resume, and safety rules.
