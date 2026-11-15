# Local AUREN observation console and MCP host

AUREN can run its observation console and authenticated MCP operator endpoint together on one workstation. Both bind to IPv4 loopback only. The dashboard and MCP ports default to **0**, so the operating system assigns free ports instead of assuming that port 8765 is available.

This is the primary single-user operator path and is not a public deployment recipe. Keep the HTTP endpoint on IPv4 loopback. The ChatGPT desktop app can launch the local stdio bridge through the shared Codex MCP configuration. This local connection uses AUREN's machine-local operator token and requires **no OAuth, OpenAI tunnel, public endpoint or remote AUREN service**. Remote ChatGPT Web access is a separate optional path; see [ChatGPT Web Setup](CHATGPT_WEB_SETUP.md).

## 1. Install and start

From an AUREN checkout or portable bundle, initialize and inspect the repository first:

```bash
python -m pip install "uvicorn[standard]>=0.35,<1"
python auren_cli.py init --project-root /absolute/path/to/your/project
python auren_cli.py doctor --project-root /absolute/path/to/your/project
```

Single-user local mode is the default and does not use or discover a runtime factory. It constructs AUREN's built-in composition with the canonical controller, durable plan/recovery stores, trusted plan dispatch, Guard, host-owned operator and assignment authorization, durable approvals, persisted budgets and continuous supervisor. The MCP host can start without a provider; task submission remains blocked until a provider is explicitly configured and the remote-data policy is enabled.

In the default single-user local mode, start both services with:

- Observation console: `http://127.0.0.1:<assigned-dashboard-port>/control-center`
- Dashboard health: `http://127.0.0.1:<assigned-dashboard-port>/api/health`
- MCP endpoint: `http://127.0.0.1:<assigned-mcp-port>/mcp`
- MCP readiness: `http://127.0.0.1:<assigned-mcp-port>/readyz`

Startup is considered successful only after both health endpoints respond. If either server exits unexpectedly, the host shuts down the other service too. Press Ctrl+C to stop the pair cleanly; POSIX service managers may send SIGTERM.

You can reserve fixed ports when your client requires stable addresses:

```bash
python auren_cli.py local \
  --project-root /absolute/path/to/your/project \
  --dashboard-port 8765 \
  --mcp-port 8766
```

Omit both port options for automatic selection. A port collision is reported at startup; AUREN does not silently bind to a different port when a fixed port was requested.

### Start directly from GitHub

You can start AUREN against a GitHub repository without creating a local checkout yourself. Run this command from an installed AUREN checkout or portable bundle:

```powershell
python auren_cli.py local --repository "https://github.com/sourabhJainR/AUREN"
```

To select a branch:

```powershell
python auren_cli.py local --repository "https://github.com/sourabhJainR/AUREN" --branch main
```

AUREN clones the repository under `%USERPROFILE%\\.auren\\workspaces\\<owner>\\<repository>` (or the configured `AUREN_HOME`), initializes its project profile on first use, and starts the same local dashboard and authenticated MCP host. Git must be installed and available on PATH. For private repositories, authenticate Git through Git Credential Manager or another secure Git credential helper before starting; do not put tokens in the URL.

An existing managed checkout is reused only when its origin matches and its worktree is clean. AUREN does not overwrite local changes, silently switch branches, or pull new commits into an existing workspace. Use a separate workspace or resolve local changes before reusing it. Only HTTPS URLs on github.com are accepted.

The repository option selects the project workspace; it does not install or update the AUREN CLI itself. The CLI must be run from an AUREN checkout or portable installation.

## Runtime profiles

AUREN project profiles default to single-user local mode. This mode uses AUREN's built-in composition root and never requires or discovers `auren_runtime_factory.py`. The root owns the canonical controller, durable stores, trusted plan dispatch and continuous supervisor.

The existing factory-backed startup is an advanced managed-mode path. Opt in explicitly:

```bash
python auren_cli.py mode --project-root /path/to/project --mode managed --runtime-factory my_package.auren_host:create_runtime --apply
python auren_cli.py local --project-root /path/to/project --runtime-factory my_package.auren_host:create_runtime
```

Return to the local profile with:

```bash
python auren_cli.py mode --project-root /path/to/project --mode single_user_local --apply
```

The profile change creates a backup and clears the managed factory reference when switching back to local. Do not run a factory-backed host from a single-user local profile. The built-in MCP execution path is currently proposal-only; it does not write repository files or perform release actions.


## Optional provider setup

The local host starts without model credentials. Its current built-in task adapter uses direct API providers for read-only proposal work only. Remote inference is disabled by default and is permitted only when the operator explicitly declares the workspace public:

```bash
export AUREN_REMOTE_PROVIDER_ORDER=openai
export OPENAI_API_KEY="<injected-from-a-secret-manager>"
export OPENAI_MODEL="your-approved-model-id"
export AUREN_LOCAL_REMOTE_INFERENCE=enabled
export AUREN_LOCAL_DATA_CLASSIFICATION=public
```

Use the provider-specific environment names in [Model Provider Integration](MODEL_PROVIDER_INTEGRATION.md). Never place API keys in project profiles, task context, MCP arguments or repository files. The accepted task binds the provider name/model, Git HEAD and Guard policy digest. If the provider or policy changes before dispatch/recovery, the task is blocked rather than silently rerouted.

The provider receives only the submitted task text. It has no repository tools and cannot modify files, merge, release or deploy. Local CLI discovery is not execution readiness; Codex, Claude Code and Gemini CLI invocation remains disabled until the scoped subprocess and workspace boundary is integrated.

## 3. Local authentication and safety

On first start, AUREN creates a random local operator token in:

- Linux/macOS: `~/.auren/mcp/local-operator.token`
- Windows: `%USERPROFILE%\.auren\mcp\local-operator.token`

If `AUREN_HOME` is set, that configured AUREN home is used instead. On POSIX systems, the token directory is restricted to owner access and the token file is created with mode `0600`. The token is reused across restarts. Delete the token file while the host is stopped to rotate it on the next start.

The local credential is workspace-scoped and grants the local operator MCP tool scopes. Keep it private. Do not paste it into source files, logs, issue trackers, or shared client configuration. Browser-origin requests are rejected; the server accepts only the loopback Host value it bound to. The dashboard remains on loopback as well.

This is workstation access control, not a hostile-code sandbox. It does not isolate arbitrary provider code from the user's filesystem, network, or operating-system identity.

## 4. Connect an MCP client directly over HTTP

If the MCP client supports Streamable HTTP and custom request headers, configure:

- Transport: Streamable HTTP
- URL: the printed `http://127.0.0.1:<port>/mcp` endpoint
- Header: `Authorization: Bearer <contents of local-operator.token>`

Use the token from the local credential file. Prefer a client secret store or environment-variable substitution if the client supports it. Avoid storing the token in a checked-in JSON/TOML file.

The local endpoint uses HTTP only because it is bound to loopback. It is not directly reachable by a hosted LLM service. ChatGPT Web can reach it through the optional OpenAI Secure MCP Tunnel without publishing the AUREN endpoint. The local AUREN credential remains a machine-local bearer token; this is not an OAuth flow.

## 5. Connect ChatGPT desktop without OAuth

ChatGPT desktop, Codex CLI and the Codex IDE extension share the Codex MCP configuration. Configure AUREN as a local stdio server. The bridge reads the existing local operator token file itself, adds the bearer header only on the loopback hop, enforces request/response size limits, and writes JSON-RPC responses to stdout. The token is not placed in the Codex config or sent to an OAuth provider.

Copy [the ChatGPT desktop local config template](../integrations/mcp/chatgpt-desktop.local.toml.example) into `~/.codex/config.toml` (Windows: `%USERPROFILE%\\.codex\\config.toml`) and replace the paths and port with your local values. Merge the `[mcp_servers.auren]` section into an existing file; do not overwrite other MCP entries. Use the exact Python executable, AUREN checkout and token file paths. The local host must be running before the client starts.

Generic MCP client configuration:

```json
{
  "mcpServers": {
    "auren": {
      "command": "python",
      "args": [
        "/absolute/path/to/AUREN/auren_cli.py",
        "mcp-proxy",
        "--url",
        "http://127.0.0.1:43125/mcp",
        "--token-file",
        "/absolute/path/to/.auren/mcp/local-operator.token"
      ]
    }
  }
}
```

Replace the example MCP port with the actual port printed by the running host. On Windows, use the full path to `python.exe` and Windows paths for the AUREN checkout and token file. The bridge intentionally accepts only an explicit IPv4 loopback `/mcp` URL; it rejects public hosts, redirects and alternate paths.

The local host must be running before the stdio client starts. If the client supports a working-directory field, set it to the AUREN checkout. Otherwise, invoking the absolute `auren_cli.py` path is sufficient.

## 6. Host compatibility notes

- **ChatGPT desktop, Codex app/CLI and IDE extension:** use the shared Codex stdio configuration above. Local OAuth is not required.
- **Local MCP clients with HTTP header support:** connect directly to the printed Streamable HTTP URL and use the local bearer token through the client's protected secret store.
- **Local MCP clients with stdio only:** use `python /path/to/AUREN/auren_cli.py mcp-proxy ...` as shown above.
- **ChatGPT Web:** it cannot reach workstation loopback directly. For a supported workspace, use OpenAI Secure MCP Tunnel to forward to the local service; AUREN remains local and the local MCP hop continues to use its bearer token, not OAuth. AUREN does not need a public endpoint. Alternatively deploy a separate hosted service if you need persistent multi-user access.
- **Other hosted/cloud-only LLMs:** use their supported private connectivity path or a separately secured remote deployment. Do not expose this local host.
- **Tools without MCP support:** use the AUREN dashboard for observation, or choose an MCP-capable local host / supported stdio adapter.

Keep consequential tool approvals enabled in the client. Client-side approval prompts do not replace AUREN's server-side authorization, policy, admission and approval checks.

## 7. Verify the complete local experience

After startup, use the exact console origin printed by `auren up` or `auren local`. The dashboard port can be selected dynamically, so do not assume it is 8765.

```bash
auren verify-local --dashboard-url http://127.0.0.1:<assigned-dashboard-port>
```

The command performs read-only checks:
- Dashboard health reports `readiness: ready`.
- The local MCP endpoint accepts the owner token and completes the supported protocol handshake.
- MCP tool discovery includes the core profile, service status, task, cancellation, approval and evidence tools.

It does not invoke tools, submit work, approve actions, or call remote model APIs. It returns a secret-free JSON report and exits non-zero if either service is unhealthy or required tools are missing. Supply `--mcp-url` and `--token-file` only when using non-default local settings.

This verifies the AUREN local service boundary. It does not prove that a separately installed Codex, Claude, Gemini or ChatGPT client has loaded the configuration; each real client still needs its own smoke test.
