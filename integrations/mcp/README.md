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

## Local self-hosted observation console and MCP

For a workstation host, run `python auren_cli.py local --project-root /absolute/path/to/project`. AUREN selects free loopback ports, creates a private workspace-scoped operator token, starts the observation console and MCP endpoint together, and verifies both health endpoints. It auto-discovers `auren_runtime_factory.py:create_runtime` in the project root or accepts `--runtime-factory module:function`. A non-empty reviewed graph is required; AUREN will not start a placeholder runtime.

If the client supports Streamable HTTP with custom headers, connect to the printed MCP URL and send `Authorization: Bearer <local-token>`. If the client supports stdio but cannot add HTTP headers, configure `python /absolute/path/to/AUREN/auren_cli.py mcp-proxy --url http://127.0.0.1:<port>/mcp --token-file /absolute/path/to/.auren/mcp/local-operator.token`. The bridge reads the token file and keeps it out of client configuration.

See [Local Self-Hosted MCP](../../docs/LOCAL_SELF_HOSTED_MCP.md) for full instructions, client examples, startup behavior, credential handling and cloud-host limitations. This local service is loopback-only; hosted LLMs require the separate HTTPS/OAuth remote deployment.

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
