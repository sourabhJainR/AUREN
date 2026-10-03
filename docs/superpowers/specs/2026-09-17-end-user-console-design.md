# End-User Console Design

## Goal

Bridge AUREN's strongest gap from an end-user perspective: make the existing execution, evidence, observability, learning, recovery, and installation state visible through one safe local console without creating a second runtime or state owner.

## User problem

AUREN already exposes repository intelligence, graph orchestration, verification, traces, experiments, learning, maintenance, checkpoints, provider discovery, and durable triggers. The user experience is fragmented across CLI commands, generated reports, JSONL/SQLite state, and documentation. A developer should not need to know the internal file layout to answer four questions: Is AUREN healthy? What is running or waiting? Why did the last task reach its result? What can I recover, inspect, or do next?

## Design

Add a dependency-free, read-only local AUREN Console served by Python's standard-library HTTP server. It reads existing machine-scoped state and never becomes an execution owner. The console has four views in one responsive page:

1. Overview: installation version/commit/hash, health checks, provider/capability availability, scheduler/maintenance status, active trigger count, latest trace/report timestamps.
2. Runs: recent trigger and trace activity with task, status, provider, duration, score summary, and evidence/report links where available.
3. Learning: deferred jobs, recent maintenance receipt, adaptive policy/tuning summary, regression/evaluation status, and last update time.
4. Recovery: checkpoint/session inventory and safe copyable resume commands. No destructive action is exposed in this first slice.

## Data ownership

The console only adapts existing sources:

- `~/.auren/active.json` and `history.jsonl` for installation state.
- `~/.auren/observability/traces.jsonl` for traces when observability is enabled.
- `~/.auren/automation/automation.db` and `~/.auren/memory/memory.db` only through narrowly scoped read-only inspection.
- `~/.auren/sessions/` for recovery checkpoints.
- Existing runtime objects are reused when they provide stable read APIs; the console must not write duplicate state.

## Safety and privacy

The server binds to loopback by default and cannot be configured through this feature to listen on a public interface. The response layer must redact known secret-like keys using the same redaction contract used by AUREN observability. No source repository files are served. No POST/PUT/DELETE endpoint is introduced. Malformed or unavailable state is shown as an explicit unavailable/unknown condition, not silently inferred.

## CLI contract

Add:

```text
python -m portable.auren_runtime console [--host 127.0.0.1] [--port 0] [--open]
python -m portable.auren_runtime status [--json]
```

`console` starts the local dashboard. Port `0` requests an OS-selected free port and prints the resulting URL. `--open` uses the platform default browser launcher when available, otherwise prints the URL. `status` returns a compact human-readable summary; `--json` returns stable machine-readable keys.

## Non-goals

This slice does not add authentication, remote hosting, mutation controls, chat UI, model selection, or a second scheduler/task engine. It also does not change AUREN's execution, verification, learning, or promotion policy.

## Acceptance criteria

- A fresh machine with no AUREN state can open the console and see a useful setup/health screen rather than a traceback.
- A machine with AUREN state sees installation, run, learning, and recovery summaries from the existing stores.
- `status --json` is deterministic in key names and never includes secrets or full prompt/task payloads.
- The console remains usable when individual state files are missing or malformed.
- The HTTP server is loopback-only by construction.
- Existing CLI commands keep their current behavior.
- Tests cover rendering, status JSON, state degradation, loopback binding, and CLI parsing.
