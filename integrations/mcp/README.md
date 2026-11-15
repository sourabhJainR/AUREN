# AUREN MCP client templates

These files are safe configuration templates. Remote examples use `https://mcp.example.com/mcp`; local examples use a workstation-owned stdio process. They intentionally contain no OAuth client secrets, API keys or local operator tokens.

- **ChatGPT desktop / Codex app / Codex CLI:** [local stdio config](chatgpt-desktop.local.toml.example). ChatGPT desktop shares the Codex MCP configuration. This path uses the local operator token file internally and does not use OAuth.
- Codex remote: `codex.config.toml.example`
- Claude Code: `claude-code.txt.example`
- Gemini CLI: `gemini.settings.json.example`
- Cursor: `cursor.mcp.json.example`
- VS Code Copilot: `vscode.mcp.json.example`
- GitHub Copilot CLI: `copilot-cli.mcp-config.json.example`
- Visual Studio 2022 17.14+ / 2026: `visual-studio.mcp.json.example`
- Cline: `cline.mcp.json.example`
- Roo Code: `roo.mcp.json.example`
- Continue: `continue.mcp.yaml.example`
- Zed: `zed.settings.json.example`

ChatGPT web is a separate connection path. It cannot read local Codex configuration or connect directly to localhost. Use the optional OpenAI Secure MCP Tunnel for a private local runtime, or deploy a hosted HTTPS MCP service. A tunnel does not require AUREN to run remotely. See [ChatGPT Web Setup](../../docs/CHATGPT_WEB_SETUP.md).

Do not enable blanket auto-approval for AUREN operator tools. Host approval prompts do not replace AUREN's server-side authorization, Guard and durable approval checks.

## Local workstation MCP

For local development, use the factory-free first-run path:

```bash
cd /path/to/your/repository
auren up
```

AUREN initializes or validates the project profile, starts the canonical local runtime and loopback MCP endpoint, and can configure supported local clients with backups. The local model task handler is proposal-only; it cannot modify repository files or run builds. See [Local Self-Hosted MCP](../../docs/LOCAL_SELF_HOSTED_MCP.md) and [Local-first Runtime and Onboarding](../../docs/LOCAL_FIRST_RUNTIME_AND_ONBOARDING.md).

## Orchestration frameworks

For OpenAI Agents SDK, AutoGen, Pydantic AI, LangChain/LangGraph and n8n examples, see [Orchestrator Integrations](../../docs/ORCHESTRATOR_INTEGRATIONS.md).

## Local stdio runtime

The local stdio bridge is `auren_cli.py mcp-proxy`. It reads the local operator token from the protected AUREN home directory and forwards MCP messages only to an explicit IPv4 loopback endpoint. It does not run OAuth and does not expose the token to the MCP host configuration. Set the printed MCP port and local paths in the ChatGPT desktop template before using it.

The alternative `portable/chat_capability.py` bridge fails closed unless `AUREN_MCP_RUNTIME_FACTORY` points to a trusted `module:function` factory that returns a configured runtime with a non-empty reviewed agent graph. Set `AUREN_PROJECT_ROOT` to the workspace bound to that process. Do not use an empty placeholder graph in a real host.
