# End-User Product Bridge Design

## Goal

Turn AUREN's existing engineering control plane into an immediately understandable end-user experience without creating parallel runtime, memory, evidence, scheduling, or policy ownership.

## Problem

AUREN already provides repository intelligence, capability routing, durable memory, observability, bounded feedback loops, durable triggers, learning, maintenance, verification, rollback, and provider integrations. Those capabilities are exposed mainly through documentation, Python modules, provider-specific installation steps, and low-level commands. A new user still has to discover how to tell whether AUREN is installed, whether their host is ready, what is active, what happened recently, and how to validate the installation safely.

## Proposed solution

Add a dependency-free user-experience layer exposed through the existing `auren_cli.py` entry point:

```text
python auren_cli.py
python auren_cli.py doctor
python auren_cli.py status
python auren_cli.py demo [PROJECT_ROOT]
```

The layer is informational by default. It must not mutate a target repository, grant permissions, change provider configuration, or bypass existing policy gates.

### Commands

#### Default invocation

With no command, show a compact AUREN welcome/status screen with:

- installed version and pinned source commit when installed;
- available commands;
- detected agent hosts/providers;
- the next useful command for an unhealthy or unconfigured installation.

The default path must never perform network access or repository mutation.

#### `doctor`

Run deterministic readiness checks and return a structured result. Human output groups checks into `PASS`, `WARN`, and `FAIL`. `--json` returns machine-readable JSON.

Checks:

- Python runtime is supported by the repository's current CI baseline (3.11+);
- current AUREN installation metadata is readable when an install exists;
- active installation contains the runtime, launcher, and canonical skill;
- installed skill destinations are readable;
- Git is available;
- detected provider CLIs are visible on PATH;
- optional Claude/Gemini/Codex skill locations are detected when present;
- the supplied project root exists and is readable;
- project source control is detected when present;
- project-local AUREN artifacts are reported as informational only, never required;
- optional extensions are reported when discoverable, but never treated as required.

Exit codes:

- `0`: no failures;
- `1`: one or more readiness failures;
- `2`: invalid command arguments or unreadable JSON state.

#### `status`

Show the durable state that an end user cares about, using existing files/databases as sources of truth:

- active AUREN version, exact commit, build hash, install time;
- installed immutable versions available for rollback;
- skill installation state for Agent Skills, Claude, and Gemini locations;
- maintenance schedule and last known maintenance runs when scheduler state exists;
- recent maintenance outcomes;
- enabled local observability state;
- recent AUREN CLI engineering reports when present;
- no more than the last five relevant events in human output;
- `--json` emits the same information as stable JSON fields.

Status is read-only and must not initialize new state stores merely by being invoked.

#### `demo`

Provide a safe, read-only end-to-end demonstration against a project root:

1. validate the root;
2. run deterministic repository intelligence for a small, fixed discovery request;
3. print the resulting repository snapshot digest, selected evidence paths, estimated token count, parse/skip counts, and explicit unknowns;
4. explain which later stages would normally execute for an implementation request, without executing or modifying project code.

The demo must work without an LLM, MCP provider, third-party extension, or network connection. It demonstrates the AUREN evidence-first context path rather than pretending to complete an engineering task.

## Architecture

Create one focused module:

`portable/user_experience.py`

Responsibilities:

- immutable dataclasses for status/check results;
- installation discovery from `~/.auren`;
- provider/host discovery using `shutil.which` and filesystem checks;
- scheduler and maintenance inspection through existing SQLite schemas without creating a second store;
- report discovery from the existing report location;
- read-only repository/demo orchestration using existing `repo_intelligence` APIs or CLI-compatible functions;
- human and JSON rendering;
- deterministic exit-code calculation.

`auren_cli.py` remains the stable entry point and only delegates user-experience commands to `portable.user_experience`. Existing build/verify/install/update/check-update/rollback behavior remains unchanged.

## Data ownership rules

- AUREN installation state remains owned by `~/.auren/active.json`, `history.jsonl`, and immutable version directories.
- Automation state remains owned by `~/.auren/automation/automation.db`.
- Observability remains owned by `~/.auren/observability`.
- Engineering reports remain owned by `.ai-harness/reports` in the existing report subsystem.
- Repository intelligence remains owned by `portable.repo_intelligence` / existing deterministic retrieval components.
- The new module reads these sources and never writes replacements.

## User experience principles

- One command should tell a new user what to do next.
- Human output should be concise first and detailed second.
- Every warning should say what it means and how to fix it.
- Optional integrations must never block the base product.
- No hidden network calls on `doctor`, `status`, or `demo`.
- No target-repository mutation from the new commands.
- JSON output must be stable enough for scripts and CI.

## Testing

Use existing unittest conventions. Add focused tests covering:

- installation discovery with missing and complete metadata;
- provider and skill detection;
- scheduler status rendering from a temporary SQLite database;
- doctor exit codes;
- stable JSON output shape;
- status read-only behavior;
- demo behavior with a temporary repository containing a few Python files;
- CLI dispatch while preserving all existing package-management commands.

## Non-goals for this slice

- graphical web dashboard;
- long-running task execution from the CLI;
- new persistent database schema;
- new provider adapters;
- changing orchestration, verification, learning, or promotion policy;
- auto-installing third-party tools;
- automatically modifying project files or provider configuration.

## Follow-on product slices

Once this control surface is stable, the next independent slices can add:

1. rich run history and replay browser;
2. web/desktop dashboard backed by the same read-only state sources;
3. guided project onboarding and configuration suggestions;
4. capability/provider health with latency and reliability history;
5. evidence graph visualization and decision explanations;
6. self-serve evaluation and benchmark comparison UI.
