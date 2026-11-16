# Gemini, Antigravity and Grok Build Integration

This guide covers three different integration paths. Keep the distinction clear:

- **Gemini CLI / Antigravity / Grok Build CLI** are AI host applications that can connect to AUREN over MCP.
- **Gemini API / Groq API / xAI API** are model inference providers used by AUREN's HybridModelRouter.
- **Grok web chat** is a chat product. Do not treat it as a local MCP client or repository executor.

A host connection does not transfer its model credentials to AUREN, and a provider API key does not grant repository access.

## 1. Start AUREN locally

Install AUREN and start it from the project directory:

```bash
auren up
```

From a source checkout:

```bash
python auren_cli.py up
```

AUREN starts the local runtime, dashboard and authenticated MCP endpoint. The endpoint binds to IPv4 loopback. Keep it local; do not expose the local MCP port to a LAN or public network.

When the supported client is detected, `auren up` previews/applies its local MCP entry with a backup and preserves unrelated settings. Current automatic configuration includes Gemini CLI's `~/.gemini/settings.json`, Antigravity CLI/IDE's `~/.gemini/config/mcp_config.json`, and Grok Build CLI's `~/.grok/config.toml` (or `$GROK_HOME/config.toml`). The local stdio bridge reads the operator token from the protected token file; the bearer token is not written into client configuration.

If you use `--no-connect`, client configuration is intentionally skipped. You can review and apply client configuration separately with:

```bash
python auren_cli.py connect
python auren_cli.py connect --apply
```

Start the local runtime before running the connect command. Configuration errors are reported per client; they do not grant access or bypass AUREN Guard.

## 2. Gemini CLI

### Install the AUREN Agent Skill

```bash
gemini skills install https://github.com/sourabhJainR/AUREN.git --path skills/ai-coding-orchestrator
```

Or, from a local AUREN checkout:

```bash
gemini skills link ./skills/ai-coding-orchestrator
```

### Connect local MCP

The simplest path is to run `auren up` with Gemini CLI installed. AUREN configures the user-level MCP entry in `~/.gemini/settings.json` using a local stdio bridge.

Check the connection:

```bash
gemini mcp list
gemini
```

Inside Gemini CLI:

```text
/mcp
/skills list
```

If you prefer manual setup, Gemini CLI supports stdio MCP in `~/.gemini/settings.json`. Use the exact Python and AUREN checkout paths for your machine:

```json
{
  "mcpServers": {
    "auren": {
      "command": "/absolute/path/to/python",
      "args": [
        "/absolute/path/to/AUREN/auren_cli.py",
        "mcp-proxy",
        "--url",
        "http://127.0.0.1:8765/mcp",
        "--token-file",
        "/absolute/path/to/.auren/mcp/local-operator.token"
      ],
      "timeout": 30000,
      "trust": false
    }
  }
}
```

Keep `trust: false`. Do not add a bearer token to `headers` or commit the local operator token. If AUREN prints a different MCP port, use that port.

### Remote MCP

For a deployed AUREN service, use its HTTPS endpoint and the OAuth flow configured by the deployment:

```bash
gemini mcp add --scope user --transport http auren https://mcp.example.com/mcp
gemini mcp list
```

Do not use a loopback URL for a cloud-hosted client. Gemini CLI, Gemini Code Assist and Antigravity are separate product surfaces; test each installed product and account tier independently.

## 3. Antigravity CLI and IDE

Antigravity has a separate MCP configuration format. Its remote endpoint field is `serverUrl`, not Gemini CLI's `httpUrl`.

The simplest local path is to run `auren up` with `agy` installed. AUREN configures `~/.gemini/config/mcp_config.json` using the local stdio bridge. Verify with `agy --version`, then open the MCP manager with `/mcp`.

If you need to configure it manually, use the following local stdio entry in `~/.gemini/config/mcp_config.json` (global) or `.agents/mcp_config.json` (workspace). Replace the paths with your actual Python, AUREN checkout and token file paths:

```json
{
  "mcpServers": {
    "auren": {
      "command": "/absolute/path/to/python",
      "args": [
        "/absolute/path/to/AUREN/auren_cli.py",
        "mcp-proxy",
        "--url",
        "http://127.0.0.1:8765/mcp",
        "--token-file",
        "/absolute/path/to/.auren/mcp/local-operator.token"
      ]
    }
  }
}
```

For a deployed AUREN endpoint, use the remote configuration below instead:

```json
{
  "mcpServers": {
    "auren": {
      "serverUrl": "https://mcp.example.com/mcp",
      "timeoutSeconds": 600
    }
  }
}
```

Open Antigravity's MCP manager with `/mcp` and verify that AUREN is connected. Complete the configured authentication flow if prompted. Do not copy Gemini CLI's `httpUrl` configuration into Antigravity.

The local configuration path is covered by AUREN's configuration tests, but a live Antigravity CLI or IDE connection has not been accepted yet. Verify the installed release and its MCP manager before treating the host as production-ready.

## 4. Grok Build CLI

Grok Build CLI is distinct from Grok web chat and from the xAI model API. It supports MCP server definitions in TOML.

### Automatic local setup

Install Grok Build CLI and ensure the `grok` executable is available on `PATH`. Start AUREN:

```bash
auren up
```

AUREN discovers Grok Build CLI and configures `~/.grok/config.toml`. If you set `GROK_HOME`, AUREN uses `$GROK_HOME/config.toml`. Existing entries are preserved. The AUREN entry is a local stdio bridge with its own token-file argument; no bearer token is copied into TOML.

Verify:

```bash
grok mcp list
grok mcp doctor
```

In an interactive Grok Build session, open the MCP panel with:

```text
/mcps
```

### Manual local setup

If automatic discovery is unavailable, add the following section to `~/.grok/config.toml`. Replace every path with the real path on your machine:

```toml
[mcp_servers.auren]
command = "/absolute/path/to/python"
args = [
  "/absolute/path/to/AUREN/auren_cli.py",
  "mcp-proxy",
  "--url",
  "http://127.0.0.1:8765/mcp",
  "--token-file",
  "/absolute/path/to/.auren/mcp/local-operator.token"
]
enabled = true
startup_timeout_sec = 30
tool_timeout_sec = 6000
```

Do not replace the token-file argument with a token value. Keep the MCP endpoint on loopback. If the entry already exists, merge the settings rather than adding a duplicate TOML table.

### Remote MCP

For a deployed endpoint, configure the remote URL in `~/.grok/config.toml`:

```toml
[mcp_servers.auren]
url = "https://mcp.example.com/mcp"
enabled = true
startup_timeout_sec = 30
tool_timeout_sec = 6000
```

Complete the OAuth flow if the deployed AUREN service advertises and requires it. Do not hard-code long-lived bearer tokens in a shared TOML file. Check the connection with `grok mcp list` and `grok mcp doctor`.

Grok web chat is not a substitute for Grok Build CLI. Supplying AUREN reports to Grok chat is a manual handoff, not a live MCP connection.

## 5. Configure Gemini, Groq or xAI as AUREN inference providers

This is separate from connecting Gemini CLI or Grok Build CLI to AUREN. The following settings configure AUREN's own direct API provider pool:

PowerShell:

```powershell
$env:AUREN_REMOTE_PROVIDER_ORDER = "groq,gemini,xai"
$env:GROQ_API_KEY = "<secret-manager-injected-value>"
$env:GROQ_MODEL = "<model-id-enabled-for-your-account>"
$env:GEMINI_API_KEY = "<secret-manager-injected-value>"
$env:GEMINI_MODEL = "<model-id-enabled-for-your-account>"
$env:XAI_API_KEY = "<secret-manager-injected-value>"
$env:XAI_MODEL = "<model-id-enabled-for-your-account>"
$env:AUREN_LOCAL_REMOTE_INFERENCE = "enabled"
$env:AUREN_LOCAL_DATA_CLASSIFICATION = "public"
```

Linux/macOS:

```bash
export AUREN_REMOTE_PROVIDER_ORDER=groq,gemini,xai
export GROQ_API_KEY="<secret-manager-injected-value>"
export GROQ_MODEL="<model-id-enabled-for-your-account>"
export GEMINI_API_KEY="<secret-manager-injected-value>"
export GEMINI_MODEL="<model-id-enabled-for-your-account>"
export XAI_API_KEY="<secret-manager-injected-value>"
export XAI_MODEL="<model-id-enabled-for-your-account>"
export AUREN_LOCAL_REMOTE_INFERENCE=enabled
export AUREN_LOCAL_DATA_CLASSIFICATION=public
```

Use model IDs enabled for the vendor account and region. Keep keys in a secret manager or protected process environment. Do not place keys in MCP configuration, project files, prompts or evidence.

Remote inference is opt-in and restricted to explicitly public/non-sensitive work. Private and restricted tasks remain local-only. The provider chain is sequential, bounded and charged to the shared supervisor budget. Model-returned tool calls are normalized but not executed.

Run a separate synthetic provider probe for each configured provider:

```bash
python scripts/provider_conformance.py --live-api --api-provider groq --json
python scripts/provider_conformance.py --live-api --api-provider gemini --json
python scripts/provider_conformance.py --live-api --api-provider xai --json
```

Each command makes one billable synthetic request. A passing result confirms only that the configured account accepted that probe. It does not establish production readiness, model quality, privacy approval or MCP host compatibility.

## 6. Guarded execution and acceptance

MCP discovery is not coding authorization. AUREN remains the authority for task admission, workspace binding, Guard, budgets, approvals, verification and evidence. A host's own auto-approval or trust setting cannot bypass AUREN policy.

For each host, record its exact version, AUREN commit/build, MCP transport, authenticated identity, discovered tool set and test evidence. At minimum:

1. Confirm the host can discover AUREN tools.
2. Call a read-only status tool and confirm the expected workspace/identity.
3. Confirm task submission is rejected without the required scope or valid workspace.
4. Submit a harmless test task with a stable idempotency key.
5. Poll to a terminal state and retrieve its evidence.
6. Confirm consequential actions remain approval-gated.
7. Test host restart, AUREN restart, timeout and reconnect behavior.
8. Confirm a host-side trust/auto-approval option does not bypass server-side Guard.

Do not mark Gemini CLI, Antigravity or Grok Build CLI as live verified until its own acceptance evidence passes. The direct provider API probe is not a substitute for this host test.

## 7. Official references

- [Gemini CLI MCP servers](https://geminicli.com/docs/tools/mcp-server/)
- [Gemini CLI skills](https://geminicli.com/docs/cli/skills/)
- [Antigravity MCP](https://antigravity.google/docs/mcp)
- [Grok Build CLI MCP servers](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/07-mcp-servers.md)
- [AUREN model provider integration](MODEL_PROVIDER_INTEGRATION.md)
- [AUREN remote MCP operations](REMOTE_MCP_OPERATIONS.md)
