# AUREN local plugin installation

The AUREN local plugin is a thin CLI entry point over the existing canonical local runtime. It does not create a second MCP server, task queue, identity store, provider router, or authorization layer.

## Install and start

From an installed AUREN checkout or portable bundle, run:

```powershell
python auren_cli.py plugin install --project-root .
```

On a machine where the `auren` launcher is on PATH:

```powershell
auren plugin install
```

This runs the existing first-run onboarding flow: validate or create the project profile, inspect provider readiness, start the shared local dashboard and authenticated MCP endpoint, and configure supported local MCP clients unless `--no-connect` is supplied.

Useful options:

- `--no-connect`: do not change local client configuration.
- `--no-browser`: do not open the local dashboard.
- `--no-chatgpt`: do not open ChatGPT Web. This does not connect ChatGPT Web to localhost.
- `--dashboard-port N` and `--mcp-port N`: select fixed local ports; defaults are 0 for the dashboard and 8765 for MCP.

The client configurator backs up files before applying changes, preserves unrelated entries, and is safe to re-run. Use `auren plugin connect` to preview configuration, or add `--apply` to write the changes.

## Check readiness

```powershell
auren plugin status --project-root .
```

This reports local prerequisites and provider configuration without printing credentials. A configured key or detected CLI is not proof of live provider/model acceptance.

## Connect or preview clients

```powershell
auren plugin connect
auren plugin connect --apply
```

The connector uses the existing local bearer credential and supported client configuration adapters. It does not grant new AUREN permissions. Server-side Guard, caller, project, approval and budget checks remain authoritative.

## Use AUREN

Once the local host is running, use the connected MCP client or the same local task API from the CLI:

```powershell
auren task submit --task "Review this repository and propose a bounded implementation plan"
auren task list
auren task status <task-id>
auren task evidence <task-id>
```

The built-in local model task handler remains proposal-only. To enable repository-changing work, configure the separate Vibe Coding executor in `~/.auren/vibe-coding/host.json`, install the Codex CLI, and set `allow_workspace_writes` to `true`. The executor works only in an isolated Git worktree. Single-user local installs use a durable host-owner approval record by default, without an interactive prompt; set `approval_mode` to `manual` if you want a separate approval step. It does not merge, publish, release or deploy to production.

After configuration, restart the local AUREN host and submit a coding task:

```powershell
auren task submit --task "Implement the requested change, run the configured tests, and report the exact evidence"
```

Check readiness with `auren plugin status --project-root .`. A missing provider CLI, invalid host config, unclean project state, or unavailable security adapter blocks executor activation rather than falling back to unrestricted execution.

## Security and hosting boundary

- Local MCP and dashboard services bind to loopback only.
- Credentials remain in the per-user AUREN state directory, not project files.
- Remote inference remains opt-in and requires public-data classification.
- Hosted ChatGPT Web cannot reach a workstation's loopback endpoint. It requires a separately deployed HTTPS MCP endpoint and OAuth configuration.

See [Local Self-Hosted MCP](LOCAL_SELF_HOSTED_MCP.md), [Local-first Runtime and Onboarding](LOCAL_FIRST_RUNTIME_AND_ONBOARDING.md), and [MCP Host Compatibility](MCP_HOST_COMPATIBILITY.md) for details.
