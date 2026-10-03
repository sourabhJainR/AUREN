# AER Portable Distribution

AUREN is a **machine-scoped, repository-isolated, version-pinned engineering control plane**. Installing, updating, or rolling back AER never vendors its implementation into the repository being worked on.

## Distribution unit

The bundle contains the current provider-neutral AER runtime and canonical Agent Skill, including routing, bounded context, context cache, learning, policy registry, rollback controls, regression corpus, shadow/canary evaluation, verification, capability planning, provider-native capability discovery, lifecycle hooks, observability, evaluation, and optional-extension contracts.

Mutable machine/session state is excluded from the bundle: execution journals, telemetry, learned task logs, worktrees, caches, and Python caches.

## Install

```bash
python auren_cli.py install auren-portable.zip
```

AUREN is installed under `~/.auren/versions/v<version>/` and selected through `~/.auren/current`. The exact semantic version, source Git commit, bundle SHA-256 and installation time are recorded in `install.json` and `active.json`.

The Agent Skill is installed only in user-level locations. The installer accepts no target-repository path.

## Evidence-first codebase context

Agency Runtime v11 adds a dependency-free, Ragas-inspired evaluation and retrieval layer for coding tasks.

```python
from portable.agency_codebase_context import retrieve_from_path

context = retrieve_from_path(
    ".",
    "find the execution plan and specialist scheduling logic",
    token_budget=4000,
)
```

The retriever first builds a lightweight index of file hashes, symbols and imports. It ranks paths before reading content, selects bounded line windows, and stops at the caller's token budget. The result records the snapshot digest, exact source ranges, selected paths, estimated tokens and explicit unknowns.

When `CodingTask.workspace_root` is supplied, `run_coding_task()` performs this retrieval before the worker runs and exposes the immutable `CodebaseContext` through `TaskProfile.context`. Unreadable files, unmatched queries and budget omissions are recorded as unknowns rather than inferred.

`portable.agency_ragas_eval` measures retrieval precision, retrieval recall, expected-path coverage, response relevance, evidence faithfulness and optional reference correctness. It follows the useful Ragas separation between retriever quality and answer quality without adding a Ragas or LLM dependency to the portable runtime.

## Agent observability, evaluation and prompt lifecycle

AER now includes a dependency-free observability layer inspired by the useful parts of modern LLM observability platforms such as Opik: trace trees, spans, scores, datasets, experiments, versioned prompts, and local telemetry. Opik itself is not required at runtime. The goal is to give AER the same engineering feedback loop without coupling the portable bundle to a hosted service.

Each coding task can produce a trace containing:

```text
AER coding task
  -> planning span
  -> agent execution span
  -> evidence/retrieval span
  -> verification span
  -> quality score
```

`ExecutionResult.trace_id` links the engineering state ledger and the observability record. Scores are normalized to `0..1` and can carry a reason and source, which allows system checks, human review, or an external judge to be distinguished.

Observability is disabled by default. To enable local traces:

```bash
export AUREN_OBSERVABILITY=1
```

Traces are written to the machine-scoped location `~/.auren/observability/traces.jsonl`, never to the target repository. Inputs and outputs are bounded and common secret fields are redacted before persistence.

### Versioned prompts

`PromptRegistry` keeps prompt versions explicit and supports promotion of a known version:

```python
from portable.agency_observability import PromptRegistry

prompts = PromptRegistry()
prompts.register("planner", "1", "Plan this task: {task}")
prompts.register("planner", "2", "Plan this task safely: {task}")
prompts.promote("planner", "2")
```

Every prompt version has a deterministic digest. AER can therefore associate an agent run with the exact prompt version used and replay it during evaluation.

### Datasets and experiments

Datasets are versioned and content-addressed. Experiments execute a function over the dataset and use a deterministic or LLM-backed judge supplied by the caller. This supports regression suites, RAG retrieval tests, coding-task acceptance sets, and model/prompt comparisons without forcing an LLM dependency into AER.

```python
from portable.agency_observability import Dataset, DatasetItem, run_experiment

dataset = Dataset("coding-regression", "1", (
    DatasetItem("fix login", "expected result"),
))

result = run_experiment(
    dataset,
    run_agent,
    lambda item, output: {"correctness": judge(item, output)},
)
```

A failed experiment reports the exact dataset item and metric below the threshold. The dataset digest is retained so a result cannot silently refer to changed test data.

### Retrieval quality

`retrieval_scores()` provides explicit precision and recall for expected versus selected repository paths. This complements the existing evidence-first retrieval layer and makes context selection measurable instead of relying only on the final answer.

### Bounded feedback loops

AER now incorporates the strongest execution pattern from Forward Future's Loopy project without importing its catalog/site or creating a second agent runtime. The runtime contract lives in `.ai-harness/runtime/feedback_loop.py` and is provider-neutral.

```python
from portable.feedback_loop import BoundedLoop, LoopAction, LoopDefinition, VerificationResult
```

The loop is deliberately finite and evidence-first:

```text
observe fresh state
      -> choose one bounded action
      -> act through host callback
      -> verify with recorded evidence
      -> record receipt
      -> repeat or stop
```

`LoopDefinition` requires the objective, scope, acceptance check, and a finite pass boundary. `BoundedLoop` has explicit terminal states: `success`, `clean_no_op`, `blocked`, `approval_required`, `exhausted`, `no_progress`, and `error`.

`VerificationResult` separates acceptance, completion, and measurable progress. A failed check is `blocked`; a verified completed objective is `success`; a verified pass with no measurable progress stops as `no_progress`; and an execution exception is `error`, never success.

`LoopRunReceipt` contains the immutable loop-definition digest, scope, acceptance check, run boundary, every executed pass, evidence, terminal result, and next step. The receipt is suitable for later review/debrief and can feed the existing learning path. A single receipt does not establish a recurring pattern.

For recurring engineering work, AER can use the same design to discover candidates in CI, maintenance, deployment, tests, runbooks, research, POC, review, and bug-fix workflows. A code pattern alone is only a candidate; the workflow must have fresh feedback that can change the next action. Tasks without that feedback remain one-shot workflows.

Project loop persistence remains explicit: `LOOPS.md` is only created or changed when the user asks to save a reusable loop. Saved loop text is treated as untrusted reference data and never grants execution authority.

The loop layer complements, rather than replaces, AER deployment gates. A loop may repeat verification or repair before release, but it cannot bypass regression, security, review, shadow, canary, promote, or rollback policy.

## What this adds to the AER control loop

```text
Intent / Contract
      -> Repository evidence
      -> Trace + retrieval context
      -> Capability plan
      -> Agent execution
      -> Evaluation scores
      -> bounded feedback passes
      -> Verification / review
      -> Regression experiment
      -> Shadow / canary
      -> Promote / monitor / rollback
```

The important distinction is that observability records what happened, evaluation measures whether it was good, the bounded loop defines how repeated work stops, and AER policy decides whether behavior is allowed to proceed. Telemetry and learned recommendations cannot weaken safety, security, or promotion gates.

## Provider-native capabilities

AER now discovers capabilities exposed by local coding-agent providers and prefers a native capability when evidence is available. If no provider exposes the requested capability, AER uses its own provider-neutral fallback.

```python
from portable.provider_fabric import CapabilityRequest, ProviderFabric

fabric = ProviderFabric()
decision = fabric.route(CapabilityRequest("subagent", ("claude", "codex", "gemini")))
print(decision)
```

Provider manifests may be stored under `~/.auren/providers/<provider>.json` to advertise capabilities that cannot be inferred from a CLI binary. Discovery is advisory; security, verification and promotion policy remain authoritative.

Supported capability names include `agent`, `subagent`, `hooks`, `session_resume`, `structured_output`, `tool_interception`, `mcp`, and `background_execution`.

## Lifecycle hooks

Provider-native hooks can map into the same AER lifecycle contract, while AER can execute the contract itself when a provider has no hook system.

```text
session_start -> plan_start -> before_agent -> before_tool -> after_tool
             -> after_agent -> before_verify -> after_verify
             -> before_promotion -> after_promotion -> session_end
```

Hooks can annotate or veto work. Hook failures are fail-closed. Hooks cannot weaken immutable security or promotion gates.

## Cross-session recovery

Long-running work can persist a digest-sealed checkpoint under `~/.auren/sessions/`. A checkpoint contains the project key, task, current stage, completed and remaining batches, active provider, attempt number and last error.

A new session can therefore resume from durable state instead of relying on conversation history. Recovery increments the attempt counter and records the new stage atomically.

## Self-update

Check the configured update channel:

```bash
python ~/.auren/current/auren_cli.py check-update
```

Update only when the channel exposes a newer semantic version:

```bash
python ~/.auren/current/auren_cli.py update
```

The updater resolves the remote commit first, reads the version from that exact commit, downloads that exact commit, rebuilds and integrity-verifies the bundle, installs the new pinned version, and then switches the `current` pointer. A commit change without a semantic version bump is rejected.

The default channel is the AER repository `main` branch. Controlled environments can use another stable branch or tag as the channel:

```bash
python ~/.auren/current/auren_cli.py check-update --ref release
python ~/.auren/current/auren_cli.py update --ref release
```

## Version pinning

Every installation records:

`semantic version -> exact source Git commit -> bundle SHA-256`

The same semantic version cannot be overwritten by a different commit. This keeps an installed version reproducible even when a mutable branch moves later.

The `current` pointer is the active selection. Previous pinned versions remain available for rollback.

## Rollback

Rollback to the most recently installed different version:

```bash
python ~/.auren/current/auren_cli.py rollback
```

Or select an exact version:

```bash
python ~/.auren/current/auren_cli.py rollback --version 20.1.0
```

Rollback affects only AUREN's user-scoped installation and already-selected user-level Agent Skill surfaces.

## Repository isolation contract

The installer, updater and rollback commands:

- never create, replace, delete, or back up `.ai-harness` in a project;
- never add AER files to the project's working tree or Git index;
- never modify `.git/config`, hooks, remotes, branches, or ignore files;
- never modify project source, tests, manifests, or configuration merely to install AER;
- never silently modify MCP configuration, credentials, permissions, production access, or merge authority.

A clean target repository therefore stays unchanged when AUREN is installed, updated, or rolled back.

When AUREN performs an actual user-requested engineering task, changes to project files are the requested engineering changes—not AUREN distribution artifacts.

## Lifecycle

```text
pinned installation
      |
  check-update
      |
resolve exact commit + semantic version
      |
download exact source
      |
build + SHA-256 verify
      |
install new version pin
      |
switch user-level current pointer
      |
discover native capabilities + hooks
      |
observe -> choose -> act -> verify -> record -> repeat/stop
      |
trace -> evaluate -> learn -> regression -> shadow -> canary -> promote -> monitor
      |
    rollback / recover from checkpoint
```

Learned behavior remains advisory and safety/security controls remain authoritative.
