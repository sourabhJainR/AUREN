# Adaptive AI Coding Orchestrator

A provider-neutral AI software-engineering control plane for Claude Code and compatible coding agents. **AER (Adaptive Engineering Runtime)** turns a task, bug, review, research question, Jira issue, or proof of concept into a repository-aware workflow with bounded context, capability routing, verification, review, durable evidence, graph orchestration, regression replay, and evidence-backed learning.

## What AER is today

The repository now has one coherent execution path from task intake through verification, learning, memory consolidation, empirical tuning, and controlled improvement.

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
| Graph execution | `portable.agency_state_graph.StateGraph` |
| Agent-team orchestration | `.ai-harness/runtime/graph_agent_team.py` |
| Capability semantics and routing | `portable.agent_capabilities` / `CapabilityFabric` |
| Durable memory | `portable.agent_capabilities.PersistentMemory` |
| Repository retrieval | `portable.agency_codebase_context.CodebaseIndex` and `portable.repo_intelligence.RepositoryMap` |
| Scheduling and run claims | `portable.automation_scheduler.AutomationScheduler` |
| Adaptive learning | `portable.adaptive_learning.AdaptiveLearningStore` |
| Empirical tuning | `portable.adaptive_tuning.AdaptiveTuner` |
| OS service lifecycle | `portable.maintenance_service` |
| Local bounded execution | `portable.local_offload.LocalOffloadBroker` |

Compatibility surfaces adapt to these owners instead of maintaining independent state.

See [`docs/CAPABILITY_MEMORY_OWNERSHIP.md`](docs/CAPABILITY_MEMORY_OWNERSHIP.md) for the capability/memory contract and [`docs/RIPWIRE_INTEGRATION.md`](docs/RIPWIRE_INTEGRATION.md) for repository-intelligence design lineage.

See [`docs/CAPABILITY_AWARE_EXECUTION.md`](docs/CAPABILITY_AWARE_EXECUTION.md) for capability-aware execution, optional installed-skill/MCP/plugin discovery, bounded selection, and graceful degradation.

## Deterministic repository intelligence

AER builds one deterministic repository snapshot and reuses it for multiple structural questions.

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

AER includes a dependency-free `StateGraph` runtime inspired by durable agent-graph patterns without taking a LangGraph runtime dependency. It provides state snapshots, reducers, conditional routing, bounded retries, checkpoints, interrupts, deterministic traces, bounded supersteps, and explicit parallel-safety controls.

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

Dependency failures are fail-closed. Read-only roles receive an explicit `patch_allowed: false` guard. The graph path is the default; `AER_GRAPH_TEAM=0` remains available for diagnostics and compatibility.

### Local resource offload

The graph/team layer can use an additive `LocalOffloadBroker` for independent local work that should not occupy the coordinator context. It detects CPU capacity, bounds concurrency, applies time/output budgets, scrubs secret-like environment variables, and can run jobs in temporary workspace copies. The broker is an execution resource, not a second orchestrator or policy owner.

```bash
python -m portable.local_offload --project-root . --workers 3 -- pytest -q
```

See [`docs/LOCAL_OFFLOAD.md`](docs/LOCAL_OFFLOAD.md) for the API and safety model.

## Engineering lifecycle and evidence

The twelve-phase engineering evolution control plane is available through `portable.engineering_evolution.EngineeringEvolutionControlPlane`, spanning canonical evidence, evidence graph, predictive feedback, AER compaction, impact/failure prediction, historical decomposition, provider calibration, cross-project validation, graduation, benchmark gates, and Ollama-independent embedded local LLM execution.

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
export AER_PROJECT_ROOT=/path/to/repo
export AER_MAINTENANCE_TIME=02:00
export AER_MAINTENANCE_TIMEZONE=Asia/Kolkata
export AER_MAINTENANCE_POLL_SECONDS=60
export AER_MAINTENANCE_BUDGET=20
export AER_MAINTENANCE_ENABLED=1
export AER_SERVICE_SCOPE=user
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

The generated unit runs the existing Python maintenance host, restarts on failure, and keeps the calendar decision in AER's durable scheduler.

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

## AER Engineering Console

AER includes a lightweight local web UI for observing engineering execution, learning, evidence, regression protection, repository/code-graph state, quality signals, benchmarks, research activity, and test growth.

The console is observation-only and reuses AER's existing state owners. It is bundled into portable AER installations; no Node.js or separate UI installation is required.

After AER is installed, start it against the repository you want to inspect:

~~~bash
python ~/.aer/current/aer_cli.py dashboard --project-root /path/to/your/repository
~~~

Windows PowerShell:

~~~powershell
python "$HOME\\.aer\\current\\aer_cli.py" dashboard --project-root "C:\\path\\to\\your\\repository"
~~~

Then open http://127.0.0.1:8765.

If the default port is busy:

~~~bash
python ~/.aer/current/aer_cli.py dashboard --project-root /path/to/your/repository --port 8876
~~~

The dashboard uses the selected project root for repository intelligence and existing AER state. It does not expose source contents, prompts, credentials, or provide write/execution APIs.

See [dashboard/README.md](dashboard/README.md) for installation-path details, Windows/Linux/macOS commands, remote access through SSH tunneling, health checks, troubleshooting, and the direct module invocation.

## AER CLI and portable distribution

The GitHub Actions `aer-portable` artifact is self-contained and includes both the outer `aer_cli.py` launcher and `aer-portable.zip`.

```bash
python aer_cli.py aer-portable.zip
```

On success, AER is installed under user-scoped `~/.aer` storage.

Build and verify a bundle locally:

```bash
git clone https://github.com/sourabhJainR/Hello_world_With_Structure.git
cd Hello_world_With_Structure
python aer_cli.py build --output aer-portable.zip
python aer_cli.py verify aer-portable.zip
```

AER records a provenance chain:

```text
semantic version -> exact source Git commit -> bundle SHA-256
```

The installed machine state is kept under:

```text
~/.aer/versions/v<version>/
~/.aer/current
~/.aer/current/install.json
~/.aer/active.json
~/.aer/automation/automation.db
~/.aer/memory/memory.db
```

Execution journals, telemetry, caches, worktrees, and Python caches remain outside the portable distribution.

## Repository isolation and safety

Installing, updating, or rolling back AER does not:

- add AER distribution files to the target repository;
- modify project source, tests, manifests, or configuration merely to install AER;
- modify Git remotes, hooks, branches, or ignore rules;
- silently modify MCP configuration, credentials, permissions, production access, or merge authority;
- allow learned behavior to weaken immutable safety or security controls.

When AER performs a user-requested engineering task, project changes are the requested engineering changes, not AER distribution artifacts.

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
Hello_world_With_Structure/
├── .ai-harness/                 # adaptive harness, policies, runtime and lifecycle
├── portable/                    # dependency-light distributable AER runtime
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

The repository intentionally contains compatibility and historical documentation surfaces. They are not independent runtime owners.

## Reference documentation

- [`docs/CAPABILITY_MEMORY_OWNERSHIP.md`](docs/CAPABILITY_MEMORY_OWNERSHIP.md) — capability, memory, and retrieval ownership.
- [`docs/RIPWIRE_INTEGRATION.md`](docs/RIPWIRE_INTEGRATION.md) — deterministic repository-intelligence lineage and boundaries.
- [`portable/LANGGRAPH_PATTERN_ALIGNMENT.md`](portable/LANGGRAPH_PATTERN_ALIGNMENT.md) — state-graph pattern mapping.
- [`portable/README.md`](portable/README.md) — portable distribution and machine-scoped lifecycle.
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — deployment and runtime integration.
- [`docs/USAGE_AND_PLATFORM_INTEGRATION.md`](docs/USAGE_AND_PLATFORM_INTEGRATION.md) — platform usage and integration.
- [`docs/ENGINEERING_WORK_REPORTS.md`](docs/ENGINEERING_WORK_REPORTS.md) — work-report and evidence flow.
- [`docs/REGRESSION_CANARY.md`](docs/REGRESSION_CANARY.md) — regression, shadow, and canary controls.
- [`docs/superpowers/specs/2026-09-17-continuous-agi-learning-loop-design.md`](docs/superpowers/specs/2026-09-17-continuous-agi-learning-loop-design.md) — continuous learning design.
- [`docs/superpowers/plans/2026-09-17-continuous-agi-learning-loop.md`](docs/superpowers/plans/2026-09-17-continuous-agi-learning-loop.md) — implementation plan and evidence checkpoints.

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

**Use AI for engineering speed; use AER for engineering discipline.**
