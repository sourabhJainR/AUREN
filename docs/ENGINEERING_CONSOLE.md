# AUREN Engineering Console

The Engineering Console is a read-only observability surface over AUREN's existing runtime state. It does not become a second orchestrator, policy engine, memory store, or execution authority.

## Run from an installed AUREN

The portable installation keeps the active build under ~/.auren/current on Linux/macOS and %USERPROFILE%\\.auren\\current on Windows.

Start the console against the repository you want to inspect:

~~~bash
python ~/.auren/current/auren_cli.py dashboard --project-root /path/to/repository
~~~

Windows PowerShell:

~~~powershell
python "$HOME\\.auren\\current\\auren_cli.py" dashboard --project-root "C:\\path\\to\\repository"
~~~

The server prints http://127.0.0.1:8765. Open that address in a browser.

For development from the source checkout:

~~~bash
python -m portable.dashboard_server --project-root . --port 8765
~~~

The full access guide is in dashboard/README.md.

The server uses only the Python standard library and is loopback-only by default.

## What it shows

- Current executions from durable `engineering_episodes`, with the dashboard event sink as a fallback.
- Task/run counts and observed duration information.
- Durable learning, maintenance, regression and SkillOpt activity when those SQLite stores exist.
- Findings, failures and verified do-not rules where existing stores expose them.
- Repository size, tests, symbols, dependency edges and the canonical AUREN code graph.
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


## Evidence-linked traces and independent review

The tracing contract is documented in [ENGINEERING_PERSONA_GOVERNANCE.md](ENGINEERING_PERSONA_GOVERNANCE.md). Trace events use a versioned schema, explicit run/task/session correlation, source/configuration revisions where supplied, phase-tagged spans, and a content digest. Span parentage is explicit; creation order does not imply nesting.

The runtime can accept an injected PersonaWorkflow with distinct planning, plan-review, implementation, and implementation-review identities. The planning review must approve a complete, digest-bound plan before the worker runs. The independent implementation review must cite evidence before the result can satisfy the promotion gate. High-risk work and release transitions fail closed if this workflow is absent.

Legacy low-risk execution remains compatible, but its trace records missing phase/review coverage. A trace is not proof of successful research or independent review merely because an event exists. The console and downstream consumers must display missing, unknown, unscored, stale, and failed states explicitly.
