# AUREN MCP client templates

These files are safe configuration templates. Replace `https://mcp.example.com/mcp` with the deployed AUREN endpoint. They intentionally contain no access tokens or API keys. Complete the host's OAuth flow after adding the server.

- Codex: `codex.config.toml.example`
- Claude Code: `claude-code.txt.example`
- Gemini CLI: `gemini.settings.json.example`
- Cursor: `cursor.mcp.json.example`
- VS Code Copilot: `vscode.mcp.json.example`
- Cline: `cline.mcp.json.example`
- Roo Code: `roo.mcp.json.example`
- Continue: `continue.mcp.yaml.example`
- Zed: `zed.settings.json.example`

ChatGPT web, ChatGPT desktop, Claude web/Desktop and Visual Studio have host UI or version-specific configuration paths; follow [MCP Host Compatibility](../../docs/MCP_HOST_COMPATIBILITY.md).

Do not enable blanket auto-approval for AUREN operator tools. Keep approval enabled for consequential actions. The host's local configuration does not replace AUREN's server-side authorization and approval checks.

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
