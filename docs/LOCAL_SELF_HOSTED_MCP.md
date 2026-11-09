# Local AUREN observation console and MCP host

AUREN can run its observation console and authenticated MCP operator endpoint together on one workstation. Both bind to IPv4 loopback only. The dashboard and MCP ports default to **0**, so the operating system assigns free ports instead of assuming that port 8765 is available.

This is a local development/operator host, not a public deployment recipe. Do not expose its HTTP endpoint through a router, tunnel, reverse proxy, or public bind. Remote deployments must use the separate HTTPS and OAuth configuration in [Remote MCP Operations](REMOTE_MCP_OPERATIONS.md).

## 1. Install and start

From an AUREN checkout or portable bundle:

```bash
python -m pip install -r requirements-local-mcp.txt
python auren_cli.py local --project-root /absolute/path/to/your/project
```

The command starts both services and prints their actual addresses:

- Observation console: `http://127.0.0.1:<assigned-dashboard-port>/control-center`
- Dashboard health: `http://127.0.0.1:<assigned-dashboard-port>/api/health`
- MCP endpoint: `http://127.0.0.1:<assigned-mcp-port>/mcp`
- MCP readiness: `http://127.0.0.1:<assigned-mcp-port>/readyz`

Startup is considered successful only after both health endpoints respond. If either server exits unexpectedly, the host shuts down the other service too. Press Ctrl+C or send SIGTERM to stop the pair cleanly.

You can reserve fixed ports when your client requires stable addresses:

```bash
python auren_cli.py local \
  --project-root /absolute/path/to/your/project \
  --dashboard-port 8765 \
  --mcp-port 8766
```

Omit both port options for automatic selection. A port collision is reported at startup; AUREN does not silently bind to a different port when a fixed port was requested.

## 2. Configure the project runtime once

MCP tools operate on the configured AUREN runtime, not on a placeholder graph. The host requires a factory that returns an initialized runtime with:

- a non-empty, reviewed agent graph;
- the canonical execution controller; and
- the durable trigger runtime.

The host automatically discovers `auren_runtime_factory.py:create_runtime` in the project root. The factory must accept `project_root` as a keyword argument. If your project uses another module or function, supply it explicitly:

```bash
python auren_cli.py local \
  --project-root /absolute/path/to/your/project \
  --runtime-factory my_package.auren_host:create_runtime
```

You can also set `AUREN_MCP_RUNTIME_FACTORY=my_package.auren_host:create_runtime` once in the host environment. Keep the factory and its provider credentials under your normal source-control and secret-management rules. Do not commit credentials.

AUREN deliberately refuses to create an empty graph or a pretend default runtime. This is the only project-specific setup step; ports, local token creation, HTTP binding, readiness checks and coordinated shutdown are automatic.

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

The local endpoint uses HTTP only because it is bound to loopback. It is not suitable for a hosted LLM service running outside the workstation. A hosted service cannot reach your machine's `127.0.0.1`; use the separately secured HTTPS/OAuth remote deployment for that case.

## 5. Use the stdio bridge when HTTP headers are unsupported

Some MCP hosts support stdio servers but do not let you configure an Authorization header for an HTTP MCP server. In that case, configure the host to launch AUREN's stdio bridge. The bridge reads the local token file itself, adds the bearer header to each local HTTP request, enforces request/response size limits, and writes JSON-RPC responses to stdout. Diagnostics go to stderr.

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

- **Local MCP clients with HTTP header support:** connect directly to the printed Streamable HTTP URL.
- **Local MCP clients with stdio only:** use `python /path/to/AUREN/auren_cli.py mcp-proxy ...` as shown above.
- **Hosted ChatGPT, hosted Claude, or another cloud-only LLM:** local loopback is not reachable from the provider's cloud. Deploy the remote MCP service behind HTTPS with OAuth; do not expose this local host.
- **Tools without MCP support:** use the AUREN dashboard for observation, or choose an MCP-capable local host / supported stdio adapter.

Keep consequential tool approvals enabled in the client. Client-side approval prompts do not replace AUREN's server-side authorization, policy, admission and approval checks.
