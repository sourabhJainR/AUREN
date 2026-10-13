# AUREN Engineering Console

The AUREN Engineering Console is a local, read-only web UI for understanding what AUREN is doing and how the engineering system is improving over time.

It reads existing AUREN state and repository intelligence. It does not become a second orchestrator, scheduler, policy engine, memory store, or execution authority.

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