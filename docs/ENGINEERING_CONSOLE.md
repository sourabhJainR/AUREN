# AER Engineering Console

The Engineering Console is a read-only observability surface over AER's existing runtime state. It does not become a second orchestrator, policy engine, memory store, or execution authority.

## Run locally

```bash
python -m portable.dashboard_server --project-root . --port 8765
```

Open `http://127.0.0.1:8765`.

The server uses only the Python standard library and is loopback-only by default.

## What it shows

- Current executions from durable `engineering_episodes`, with the dashboard event sink as a fallback.
- Task/run counts and observed duration information.
- Durable learning, maintenance, regression and SkillOpt activity when those SQLite stores exist.
- Findings, failures and verified do-not rules where existing stores expose them.
- Repository size, tests, symbols, dependency edges and the canonical AER code graph.
- Repository quality signals and recorded verification/evidence counts without inventing a synthetic quality score.
- Benchmark and regression activity.
- Research/capability experiment records where present.
- Test files and detected test cases.

## Event contract

Lifecycle observers can call `EngineeringDashboard.record_event(...)` with a stable `run_id`. The latest event for a run identifies active work. A terminal status such as `completed` or `failed` removes it from the active view on the next refresh.

This is an observation sink only. Existing execution, verification, learning and promotion owners remain authoritative.

## Data safety

The dashboard exposes local operational metadata, not source-file contents, secrets, prompts or credentials. Repository intelligence is used for counts and graph metadata. The server is loopback-only by default and has no write APIs.

If an underlying store is absent or does not contain a metric, the UI displays the absence rather than fabricating a value.
