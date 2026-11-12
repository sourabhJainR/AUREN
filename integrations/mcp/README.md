# AUREN MCP client templates

These files are safe configuration templates. Replace `https://mcp.example.com/mcp` with the deployed AUREN endpoint. They intentionally contain no access tokens or API keys. Complete the host's OAuth flow after adding the server.

- Codex: `codex.config.toml.example`
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

ChatGPT web and Claude web/Desktop use hosted plugin/connector setup. ChatGPT desktop uses Settings → MCP servers and shares the Codex host configuration. Follow [MCP Host Compatibility](../../docs/MCP_HOST_COMPATIBILITY.md) for each host's exact setup and current limitations.

Do not enable blanket auto-approval for AUREN operator tools. Keep approval enabled for consequential actions. The host's local configuration does not replace AUREN's server-side authorization and approval checks.

## Local workstation MCP

For local development, use the factory-free first-run path:

```bash
cd /path/to/your/repository
auren up
```

AUREN initializes or validates the project profile, starts the canonical local runtime and loopback MCP endpoint, and can configure supported local clients with backups. The local model task handler is proposal-only; it cannot modify repository files or run builds. See [Local Self-Hosted MCP](../../docs/LOCAL_SELF_HOSTED_MCP.md) and [Local-first Runtime and Onboarding](../../docs/LOCAL_FIRST_RUNTIME_AND_ONBOARDING.md).

A loopback endpoint is not reachable from ChatGPT web or another hosted service. For hosted clients, AUREN needs a separately deployed HTTPS remote MCP service and external OAuth authorization server. See [ChatGPT Web Setup](../../docs/CHATGPT_WEB_SETUP.md) and [Remote MCP Operations](../../docs/REMOTE_MCP_OPERATIONS.md).

## Orchestration frameworks

For OpenAI Agents SDK, AutoGen, Pydantic AI, LangChain/LangGraph and n8n examples, see [Orchestrator Integrations](../../docs/ORCHESTRATOR_INTEGRATIONS.md).

## Local stdio runtime

The local stdio bridge is `portable/chat_capability.py`. It now fails closed unless `AUREN_MCP_RUNTIME_FACTORY` points to a trusted `module:function` factory that returns a configured runtime with a non-empty reviewed agent graph. Set `AUREN_PROJECT_ROOT` to the workspace bound to that process. Do not use an empty placeholder graph in a real host.

Example environment (supply the actual factory from your deployment package):

```bash
export AUREN_MCP_RUNTIME_FACTORY="my_auren_runtime:create_runtime"
export AUREN_PROJECT_ROOT="/absolute/path/to/workspace"
python /absolute/path/to/AUREN/portable/chat_capability.py
```
