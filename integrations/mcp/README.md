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
