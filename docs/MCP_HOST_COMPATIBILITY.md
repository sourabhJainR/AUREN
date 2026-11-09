# AUREN MCP Host Compatibility

AUREN's remote endpoint advertises MCP `2026-07-28` stateless mode and retains the `2025-11-25` handshake for older hosts. Modern clients should negotiate with `server/discover`; legacy clients may continue using `initialize`. The endpoint validates `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name` on modern requests.

AUREN's remote operator service exposes one MCP endpoint. An MCP-capable host connects to that endpoint; the host's model provider remains independently selectable. This is the lowest-friction integration path for IDEs and orchestration clients.

The examples use `https://mcp.example.com/mcp`. Replace it with the deployed AUREN endpoint. The remote service and its external OAuth authorization server must be deployed first. Do not paste long-lived bearer tokens into shared configuration files.

Safe configuration templates for the CLI and IDE hosts are under [`integrations/mcp/`](../integrations/mcp/README.md). They contain placeholders only and no credentials.

## Compatibility status

| Host | Native connection path | Repository readiness | Remaining proof |
|---|---|---|---|
| ChatGPT web | Custom MCP app/plugin with OAuth | Documented; remote service in PR #1313 | Workspace/plan eligibility, OAuth registration and live tool acceptance |
| ChatGPT desktop app | Settings → MCP servers; Streamable HTTP or stdio; OAuth | Documented through the shared Codex host configuration | Connect to the deployed AUREN endpoint, authenticate, restart the app and verify `/mcp` tool discovery |
| Codex CLI, Codex app and IDE extension | Shared `~/.codex/config.toml` or `codex mcp add` | Documented | OAuth login, tool discovery, approval and cancellation |
| Claude chat web | Remote MCP connector / custom connector | Documented | External OAuth provider and live connector acceptance |
| Claude Desktop | Remote MCP connector | Documented | Live OAuth and tool acceptance |
| Claude Code CLI | `claude mcp add --transport http` | Documented | Live version and permission-mode acceptance |
| Claude Code VS Code extension | Same Claude Code MCP configuration | Documented | Extension smoke test |
| Gemini CLI | `gemini mcp add --transport http` or `settings.json` | Documented | OAuth and tool discovery |
| Gemini Code Assist | Gemini CLI MCP integration in Agent mode | Partial | Test the installed extension/plan separately |
| Cursor | `mcpServers` in `mcp.json` | Documented | OAuth and local/cloud/SSH acceptance |
| VS Code GitHub Copilot | `.vscode/mcp.json` | Documented | Agent-mode tool discovery and approval |
| Visual Studio GitHub Copilot | `.mcp.json` or `%USERPROFILE%\\.mcp.json` with top-level `servers`; remote URL and OAuth | Repository configured | Visual Studio 2022 17.14+ / 2026 live AUREN OAuth, tool enablement and approval acceptance |
| Cline IDE / CLI | Remote Streamable HTTP MCP | Documented | OAuth and tool approval acceptance |
| Roo Code | Global or project MCP configuration | Documented | OAuth and workspace permission acceptance |
| Continue | MCP in Agent mode; `.continue/mcpServers` | Documented | Remote OAuth and tool acceptance |
| Zed Agent | Remote MCP server in AI settings | Documented | OAuth and approval acceptance |
| Zed External Agents | ACP agent path; agent may own its MCP config | Partial | Validate AUREN config in the selected external agent |
| OpenHands local | Local deployment with Streamable HTTP MCP | Partial | Local-only acceptance; do not assume cloud support |
| Windsurf / Devin Local | Product-version-specific MCP configuration | Partial | Confirm current product/config path; no live acceptance |
| Other MCP hosts | Streamable HTTP plus supported OAuth | Protocol-level path | Host-specific protocol/auth acceptance |

"Documented" means a setup path is provided, not that a vendor application was installed and tested. All entries remain `live_verified=false` until a real acceptance receipt is attached.

## Agent orchestration frameworks

The same remote endpoint is available to frameworks that implement MCP clients. Version-pinned examples and auth guidance are maintained in [Orchestrator Integrations](ORCHESTRATOR_INTEGRATIONS.md).

| Framework | MCP client implementation | Status |
|---|---|---|
| OpenAI Agents SDK | `MCPServerStreamableHttp` / hosted MCP | Example documented; live acceptance pending |
| AutoGen | `StreamableHttpServerParams` / `mcp_server_tools` | Example documented; live acceptance pending |
| Pydantic AI | `MCPToolset` | Example documented; live per-user auth acceptance pending |
| LangChain / LangGraph | `langchain-mcp-adapters` | Adapter documented; pinned sample acceptance pending |
| n8n | MCP Client Tool node | Workflow setup documented; live acceptance pending |
| CrewAI | No AUREN-specific connector verified | Not yet verified; do not claim native support |

These frameworks supply the agent loop and model selection. AUREN supplies governed engineering execution and its own authorization, approvals, idempotency and evidence. A model's tool-use capability does not grant additional AUREN permissions.

## OpenAI: ChatGPT, Codex and desktop

### Codex CLI, app and IDE extension

Codex clients share MCP configuration. For the CLI:

```bash
codex mcp add auren --url https://mcp.example.com/mcp
codex mcp login auren
codex mcp list
```

Equivalent `~/.codex/config.toml`:

```toml
[mcp_servers.auren]
url = "https://mcp.example.com/mcp"
```

The Codex app, CLI and IDE extension share the Codex host configuration. Authenticate from the host UI or `codex mcp login` when prompted. ChatGPT desktop's Settings → MCP servers uses this same Codex host configuration.

### ChatGPT web

ChatGPT web does not read local Codex configuration. It uses remote MCP tools through an installed MCP-backed plugin. Package and publish AUREN through the ChatGPT Plugin Creator/Sites flow, then install and connect it in the workspace. This plugin packaging and publication step is still open. ChatGPT desktop has a separate Settings → MCP servers flow and shares MCP configuration with Codex CLI and the IDE extension. The AUREN service requires an external OAuth authorization server that supports the client registration and resource/audience behavior expected by each host. Workspace, plan and administrator settings affect availability; do not infer hosted access from desktop/Codex access.

### OpenAI API / Responses API

The OpenAI Responses API can connect to remote MCP using `server_url` and an optional authorization token. AUREN's approval policy must remain enforced server-side. Do not set automatic approval for consequential AUREN tools merely because the API supports an approval option.

## Anthropic: Claude web, Desktop and Code

### Claude Code CLI and VS Code extension

```bash
claude mcp add --transport http auren https://mcp.example.com/mcp
claude mcp list
```

For user scope, use the `--scope user` option supported by the installed Claude Code version. The VS Code extension uses the Claude Code host configuration.

### Claude chat web and Claude Desktop

Use the product's remote connector/custom connector UI and authenticate with the configured OAuth provider. This connection gives the chat product remote AUREN tools; it does not grant local filesystem access. Repository work still runs on the AUREN-configured host.

## Google Gemini

### Gemini CLI

```bash
gemini mcp add --transport http auren https://mcp.example.com/mcp
gemini mcp list
```

Or add to `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "auren": {
      "httpUrl": "https://mcp.example.com/mcp",
      "timeout": 30000,
      "trust": false
    }
  }
}
```

Keep `trust: false` unless the operator has reviewed the full AUREN tool surface and intentionally changed the host's confirmation policy.

### Gemini Code Assist and Antigravity

Treat these as separate host acceptance targets from Gemini CLI. Their MCP configuration and account/plan support can differ. Verify the installed product version, sign-in path and tool approvals before marking either live verified.

## Cursor

Add a remote server in Cursor's MCP settings or its `mcp.json`:

```json
{
  "mcpServers": {
    "auren": {
      "url": "https://mcp.example.com/mcp"
    }
  }
}
```

Complete OAuth when Cursor prompts. Test local Agent, Cloud Agent, SSH and remote worker contexts separately; a local configuration is not proof that a cloud agent can reach the same endpoint.

## VS Code and Visual Studio with GitHub Copilot

### VS Code

Create `.vscode/mcp.json`:

```json
{
  "servers": {
    "auren": {
      "type": "http",
      "url": "https://mcp.example.com/mcp"
    }
  }
}
```

Open Copilot Chat in Agent mode, enable the AUREN server in the tools picker and verify the discovered tools.

### Visual Studio

Visual Studio 2022 version 17.14+ and Visual Studio 2026 support remote MCP servers in GitHub Copilot Agent mode. Create `.mcp.json` at the solution root or `%USERPROFILE%\\.mcp.json` and use the top-level `servers` object. Authenticate through the Visual Studio MCP authentication UI, then enable AUREN in the Agent tools picker. The repository includes `integrations/mcp/visual-studio.mcp.json.example`. This is a native MCP host connection, not a dedicated AUREN Visual Studio extension. Live acceptance is still required.

## Cline

Use the Cline MCP Servers UI or CLI wizard and choose remote Streamable HTTP. A representative config entry is:

```json
{
  "mcpServers": {
    "auren": {
      "type": "streamableHttp",
      "url": "https://mcp.example.com/mcp",
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

Keep `autoApprove` empty for AUREN's consequential operator tools. Use the host's OAuth authorization flow rather than a shared static token.

## Roo Code

Roo Code supports global MCP settings and project-level `.roo/mcp.json`. Use its MCP UI to add the remote Streamable HTTP URL and complete the authentication flow. Keep approval enabled for submit, cancel, resume, approval-decision and admission-control tools.

## Continue

MCP works in Continue Agent mode. Put the server definition under `.continue/mcpServers/` using the JSON/YAML format supported by the installed version. A YAML example:

```yaml
name: AUREN MCP
version: 1.0.0
schema: v1
mcpServers:
  - name: auren
    type: streamable-http
    url: https://mcp.example.com/mcp
```

Continue's Agent mode and its model configuration are separate concerns. AUREN's MCP connection does not change the selected inference provider.

## Zed

Add a remote MCP server in Settings → AI → MCP Servers, or in the Zed settings file:

```json
{
  "context_servers": {
    "auren": {
      "url": "https://mcp.example.com/mcp"
    }
  }
}
```

When no Authorization header is configured, Zed can use the standard MCP OAuth flow. Zed Agent uses Zed-configured model providers; External Agents generally own their own model/auth/tool configuration. Verify each path independently.

## OpenHands, Windsurf/Devin and other hosts

- OpenHands: validate only the local deployment path first. Do not assume a cloud-hosted product supports custom MCP servers.
- Windsurf/Devin: product naming and agent configuration have changed. Confirm whether the installed client is legacy Cascade or Devin Local before choosing its MCP configuration file.
- Goose, OpenCode, Aider and other clients: use the native MCP client configuration only when the installed version documents Streamable HTTP and the required OAuth flow. Otherwise use the local stdio bridge or AUREN CLI where supported.


## Official host references

- [ChatGPT custom MCP servers](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)
- [ChatGPT and Codex MCP host configuration](https://learn.chatgpt.com/docs/extend/mcp)
- [Codex CLI MCP commands](https://learn.chatgpt.com/docs/cli/reference)
- [Anthropic MCP support](https://docs.anthropic.com/en/docs/mcp)
- [Claude Code CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-usage)
- [Gemini CLI MCP servers](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md)
- [Cursor MCP](https://prod.cursor.com/docs/mcp)
- [Visual Studio MCP servers](https://learn.microsoft.com/en-us/visualstudio/ide/mcp-servers?view=visualstudio)
- [Cline MCP](https://github.com/cline/cline/blob/main/docs/mcp/mcp-overview.mdx)
- [Roo Code MCP](https://github.com/RooCodeInc/Roo-Code-Docs/blob/main/docs/features/mcp/using-mcp-in-roo.mdx)
- [Continue MCP](https://docs.continue.dev/customize/deep-dives/mcp)
- [Zed MCP](https://zed.dev/docs/ai/mcp)

## Acceptance checklist

A host is not production-compatible until all of these are recorded for its exact version:

1. Server connects over HTTPS with valid host and Origin behavior.
2. OAuth discovery, authorization, token audience, refresh and revocation work.
3. Tool discovery returns the expected scope-filtered AUREN tools.
4. Read-only status/evidence tools work for a read-only principal.
5. Task submission rejects missing scope, wrong workspace, invalid schema and replayed/changed idempotency payloads.
6. Consequential actions require the correct server-side approval; host auto-approval cannot bypass AUREN Guard.
7. Cancellation reports a request until the supervisor confirms its terminal outcome.
8. Network interruption, timeout, host restart and token revocation produce bounded, auditable outcomes.
9. A sanitized acceptance receipt records host version, MCP protocol version, OAuth issuer, tool set digest and test result.

The service implementation currently supports stateless Streamable HTTP JSON responses and does not implement the MCP Tasks extension or SSE streaming. A host that requires either feature is not compatible with this transport profile until that capability is added and tested.
