# AUREN Engineering Console

The AUREN Engineering Console is a local, read-only web UI for understanding what AUREN is doing and how the engineering system is improving over time.

It reads existing AUREN state and repository intelligence. It does not become a second orchestrator, scheduler, policy engine, memory store, or execution authority.


## Live session control center

The main Engineering Console includes a **Sessions** tab backed by the read-only `GET /api/sessions` endpoint. It polls every four seconds and projects the existing project-scoped session checkpoints, supervisor progress journal, and digest-verified plan-run store. It does not create a second session database or execute tasks.

The view separates active, completed, blocked, and unverified sessions; includes task/stage/provider/attempt timestamps and completed or remaining batches; and shows supervisor task progress, stale-state guidance, and plan task counts. Search and status filters are browser-side. A failed refresh is shown as unavailable and does not clear or complete previously observed work.

The endpoint uses an allowlist projection. It does not return session intent, raw error text, task messages, plan goals, prompts, or credentials. Checkpoint digests and safe identifiers remain visible for correlation. The endpoint remains behind the dashboard's loopback-origin guard and bounded atomic JSON response limit. If the response exceeds that limit, the API fails closed rather than truncating the session list.

## Unified AUREN × Muxin control center

The unified control center is served by the installed AUREN dashboard server at **http://127.0.0.1:8765/control-center**. Start the server first; keep its terminal/process running while using the browser.

### Install or update, then start

1. Install/update AUREN using the repository's supported installer or release bundle. The active installation is under `~/.auren/current` on Linux/macOS or `%USERPROFILE%\.auren\current` on Windows.
2. Open a terminal in the repository you want AUREN to work on.
3. Start the dashboard, passing that repository as the project root:

~~~bash
python ~/.auren/current/auren_cli.py dashboard --project-root /path/to/your/repository
~~~

Windows PowerShell:

~~~powershell
python "$HOME\.auren\current\auren_cli.py" dashboard --project-root "C:\path\to\your\repository"
~~~

4. Open **http://127.0.0.1:8765/control-center**. If you selected a custom port, use that port in the URL. The control center tries the same-origin `/api/control-center` endpoint automatically; use **Connect local console** if you need to reconnect.

For a source checkout, run `python auren_cli.py dashboard --project-root .` from the checkout and open the same `/control-center` path.

### Working with the control center

- **Overview:** check active/recent sessions, progress, learnings, model discovery, and evidence gates. Refresh to request a fresh snapshot.
- **Sessions:** open **Details** to inspect reported stage, changed paths, blocker, owner, and checkpoint/evidence digest. Export JSON for a point-in-time snapshot.
- **Learnings:** search source-backed memory records. A missing source, confidence, verification flag, or evidence ID remains missing; the dashboard does not manufacture evidence or promote a learning.
- **Local LLMs / routing:** model discovery reports what the local endpoint exposes. The routing action submits a task to `POST /api/route`; it currently uses coding capability and private privacy mode, with bounded attempts and timeout. Private/restricted tasks must not be sent to a remote fallback. Remote execution is not enabled by default.
- **Evidence & gates:** treat the view as an observability aid, not as proof that CI, review, independent evaluation, or a merge gate passed. Confirm those in the repository's actual CI and pull request.
- **New session:** in this control-center prototype, this creates a browser-local planning card only. It does **not** start an AUREN agent run. Do not mistake it for execution.
- **Demo and live data:** when no API is reachable, the UI may show example data. Confirm the connection banner says connected before relying on session details.

### Using AUREN for engineering work

Use AUREN's CLI/orchestrator to start and govern engineering tasks; use the control center to observe the resulting runtime state. Set the repository root correctly, state the task and acceptance criteria, let AUREN run its policy and verification gates, and check the resulting tests/CI evidence before accepting changes. Do not use a dashboard progress percentage or model response alone as completion evidence.

Keep the server bound to `127.0.0.1` for normal use. Do not expose it on a public or untrusted network. Never put credentials, API keys, tokens, or private prompts in session notes or exported snapshots.

## What the UI shows

- Current and recently active EngineeringEpisodes
- Task and run counts
- Per-task execution duration
- Durable learnings and SkillOpt activity when the corresponding stores exist
- Findings, failures and verified DO/DON'T rules exposed by existing stores
- Regression and benchmark activity
- Research and capability experiment records
- Repository file and symbol counts
- Actual observed repository dependency/code-graph edges
- Codebase quality signals and verification/evidence counts
- Test files, detected test cases and explicitly recorded tests-added telemetry
- AUREN usage and execution history

The UI refreshes automatically. Repository intelligence is cached briefly so refreshing the browser does not rebuild the code graph on every request.

If a source store does not exist or does not contain a metric, the console reports the absence rather than inventing a value.

## Access after AUREN is installed

AUREN installs its active immutable build under:

- Linux/macOS: ~/.auren/current
- Windows: %USERPROFILE%\\.auren\\current

The active installation contains the dashboard server and UI assets.

### Recommended command

From any terminal:

~~~bash
python ~/.auren/current/auren_cli.py dashboard --project-root /path/to/your/repository
~~~

Windows PowerShell:

~~~powershell
python "$HOME\\.auren\\current\\auren_cli.py" dashboard --project-root "C:\\path\\to\\your\\repository"
~~~

The server prints:

~~~text
AUREN Engineering Console: http://127.0.0.1:8765
~~~

Open that address in a browser.

The default binding is 127.0.0.1, so the console is available only on the local machine.

### If auren_cli.py is already on PATH

~~~bash
auren_cli.py dashboard --project-root /path/to/your/repository
~~~

If the launcher is not on PATH, use the full path shown above.

## Run directly from the installed AUREN runtime

The equivalent module command is:

~~~bash
PYTHONPATH="$HOME/.auren/current" python -m portable.dashboard_server \
  --project-root /path/to/your/repository \
  --host 127.0.0.1 \
  --port 8765
~~~

Windows PowerShell:

~~~powershell
$env:PYTHONPATH="$HOME\\.auren\\current"
python -m portable.dashboard_server `
  --project-root "C:\\path\\to\\your\\repository" `
  --host 127.0.0.1 `
  --port 8765
~~~

The CLI form is preferred because it resolves the active AUREN installation directly.

## Choosing the project root

--project-root must point to the repository whose AUREN activity you want to inspect.

Example:

~~~bash
python ~/.auren/current/auren_cli.py dashboard --project-root ~/work/Hello_world_With_Structure
~~~

The dashboard uses that directory for:

1. Repository Intelligence and the code graph
2. .auren dashboard events and AUREN SQLite state
3. state and .ai-harness SQLite stores when present
4. Engineering episode state
5. Test and repository structure discovery

Do not point --project-root at ~/.auren/current unless the installed AUREN runtime itself is the repository you want to inspect.

## Custom port

If port 8765 is already in use:

~~~bash
python ~/.auren/current/auren_cli.py dashboard \
  --project-root /path/to/repository \
  --port 8876
~~~

Then open http://127.0.0.1:8876.

## Custom host

Keep 127.0.0.1 for normal use.

A different host can be supplied when a controlled environment requires it:

~~~bash
python ~/.auren/current/auren_cli.py dashboard \
  --project-root /path/to/repository \
  --host 0.0.0.0 \
  --port 8765
~~~

This changes the exposure boundary. Do not bind to a non-loopback address on an untrusted network without an appropriate authenticated or restricted network boundary.

## Health check

While the server is running:

~~~bash
curl http://127.0.0.1:8765/api/health
~~~

Expected response:

~~~json
{"status":"ok"}
~~~

The dashboard data endpoint is http://127.0.0.1:8765/api/dashboard.

## Starting the console on another machine

The UI is a local process, not a hosted service.

On the machine where AUREN is installed:

1. Start the dashboard command.
2. Keep the terminal/process running.
3. Open the printed URL in a browser on that machine.

For remote access, prefer an authenticated SSH tunnel rather than exposing the dashboard directly.

Example:

~~~bash
ssh -L 8765:127.0.0.1:8765 user@auren-host
~~~

Then, on the local browser, open http://127.0.0.1:8765.

## Running from an AUREN source checkout

For development:

~~~bash
cd AUREN
python auren_cli.py dashboard --project-root .
~~~

Or:

~~~bash
python -m portable.dashboard_server --project-root . --port 8765
~~~

## Data and security boundary

The Engineering Console is observation-only.

It does not:

- execute engineering tasks
- modify source files
- modify AUREN policy
- promote learning
- modify regression cases
- change credentials or permissions
- expose source-file contents through the dashboard API
- expose prompts or credentials
- create a second scheduler

The dashboard reads underlying SQLite stores in read-only mode and uses repository intelligence for structural metadata.

The server is loopback-only by default.

## Troubleshooting

### AUREN runtime not found

Use the installed launcher explicitly:

~~~bash
python ~/.auren/current/auren_cli.py dashboard --project-root /path/to/repository
~~~

Check that the active installation exists:

~~~bash
ls ~/.auren/current
~~~

Windows:

~~~powershell
Get-ChildItem "$HOME\\.auren\\current"
~~~

### Port already in use

Choose another port:

~~~bash
python ~/.auren/current/auren_cli.py dashboard --project-root /path/to/repository --port 8876
~~~

### Dashboard opens but shows little or no historical data

Check that --project-root is the repository where AUREN has been running. The console does not manufacture history. Missing stores or missing telemetry are shown as unavailable.

### Code graph looks empty

Check that the project root contains the source repository and that the repository is readable by the AUREN process. The graph intentionally excludes .auren and state telemetry directories.

## Updating AUREN

The console is bundled with AUREN. After installing or updating AUREN to a build containing the Engineering Console, the active installation under ~/.auren/current (or the Windows equivalent) contains the matching UI, server and runtime.

No separate UI package or Node.js installation is required.

### Capability-aware local model selection

The router prefers explicit capability metadata from the local model discovery response and falls back to deterministic model-name heuristics only when metadata is absent. Models with malformed capability declarations are excluded rather than assumed capable. Within the same capability tier, the router preserves the provider's configured discovery order so intentional local priority (and existing operator configuration) remains meaningful. Discovery failures are surfaced as route outcomes; private/restricted tasks never fall back to remote execution. These controls do not imply model quality: production routing should additionally record task-specific quality evaluations alongside latency and resource usage before treating a model as preferred.

## Agent observability

The console includes an **Observability** tab backed by the read-only `GET /api/observability` endpoint. It reports aggregate trace counts, phase coverage, provider counts, duration summaries, corrupt-line counts, and recent outcomes. The response deliberately excludes raw prompts, span inputs, and outputs.

The endpoint reads the local runtime trace journal at the canonical AUREN runtime home. Missing storage is reported as unavailable; stale in-progress runs are labeled stale rather than complete. The journal may contain multiple append-only snapshots for one run; the projection uses the latest snapshot per trace ID.

The schema and persona requirements are documented in [ENGINEERING_PERSONA_GOVERNANCE.md](../docs/ENGINEERING_PERSONA_GOVERNANCE.md).

## Durable task progress

The console exposes progress-journal snapshots emitted by the execution supervisor: queueing, dispatch, preflight, handler dispatch, heartbeat, quota waits, recovery, and terminal outcomes. Stale and unknown states remain visible, with a next action where available. SQLite task state remains authoritative; the journal is a durable projection and a missing journal is not treated as success.

Provider-backed crew events are projected when the host registers adapters with `progress_journal=controller.progress_journal` (PRs [#775](https://github.com/sourabhJainR/AUREN/pull/775) and [#776](https://github.com/sourabhJainR/AUREN/pull/776)). The active graph must pass the supervisor-issued task ID to dispatch; the alternate harness graph API accepts `supervisor_task_id`. If either wiring input is missing, provider lifecycle events cannot be attributed to the canonical task. The dashboard does not create provider credentials or infer task identity. On controller startup, PR [#777](https://github.com/sourabhJainR/AUREN/pull/777) reconciles journal projections against the SQLite task row and executive receipt. Store disagreement is shown as `blocked`; expired or missing leases are shown as `recovering`. An incomplete bounded scan appears in health findings with a follow-up action, and no handler is replayed automatically.

The same journal can be inspected without opening the dashboard using `python auren_cli.py progress --json`; the command is read-only and preserves JSON output without creating a work report.
