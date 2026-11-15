# AUREN local ChatGPT connection (Windows)

This is the supported local-first path for using AUREN from ChatGPT Web without publishing your laptop's MCP port.

## What this provides

- AUREN clones or opens one selected GitHub project on the laptop.
- AUREN starts its canonical local runtime, Guard, dashboard and authenticated MCP service.
- OpenAI's official `tunnel-client` forwards ChatGPT MCP requests to the local AUREN MCP service over an outbound-only connection.
- The tunnel client receives the local operator bearer token through a protected file reference. The token is not copied into the tunnel profile.
- The OpenAI runtime API key is supplied only through the process environment and is not written to AUREN config.
- Stopping the foreground command stops both the tunnel client and AUREN local host.

The tunnel is transport only. It does not create a second controller, agent runtime, memory store or approval authority. AUREN Guard and project scope remain authoritative.

## Requirements

1. Windows 10/11 with PowerShell 7 or Windows PowerShell 5.1.
2. AUREN source checkout with the local runtime code. Clone it once:

   ```powershell
   git clone https://github.com/sourabhJainR/AUREN.git
   Set-Location .\AUREN
   ```

   The helper runs the checked-out `auren_cli.py` directly. After a packaged release containing this command is published, the installed `auren` launcher can be used instead.
3. Git installed and authenticated for private repositories. AUREN uses the local Git credential helper; it does not ask ChatGPT for your GitHub password or token.
4. The official OpenAI `tunnel-client` Windows binary. Download it from [OpenAI Platform Tunnels](https://platform.openai.com/settings/organization/tunnels), then add its directory to PATH. Do not download a binary from an unofficial mirror.
5. A tunnel ID and a runtime API key from the Platform tunnel setup. The runtime key needs Tunnels Read + Use permission; do not use an admin key.
6. A ChatGPT workspace that permits custom MCP apps. Current OpenAI documentation lists full MCP (including write actions) for Business and Enterprise/Edu. Pro supports read/fetch only. Free is not currently listed as eligible. See [OpenAI's connection requirements](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt).

## Start AUREN for a GitHub repository

Clone the AUREN repository and open PowerShell in its root. The helper can run the source checkout directly; a separate AUREN global install is not required for this path:

```powershell
git clone https://github.com/sourabhJainR/AUREN.git
Set-Location .\AUREN
.\scripts\start-auren-chatgpt.ps1 -Repository "https://github.com/OWNER/REPOSITORY"
```

For a specific branch:

```powershell
.\scripts\start-auren-chatgpt.ps1 -Repository "https://github.com/OWNER/REPOSITORY" -Branch "feature/my-work"
```

For a repository already cloned on the laptop:

```powershell
.\scripts\start-auren-chatgpt.ps1 -ProjectRoot "C:\Work\MyProject"
```

Install the local HTTP dependency if it is not already available in the checkout's Python environment:

```powershell
python -m pip install "uvicorn[standard]>=0.35,<1"
```

The script prompts for the tunnel ID and runtime key. The key is entered as a secure PowerShell prompt and passed to the tunnel process through its environment. It is not an argument, checked-in setting or profile value. The tunnel ID and key are created in the OpenAI Platform organization, not in ChatGPT settings.

Keep the PowerShell window open. AUREN uses port 8765 for the local MCP endpoint by default. If that port is already occupied, stop the other process or use a different port by invoking `auren chatgpt-tunnel run --mcp-port <port>` directly.

## Add the tunnel in ChatGPT Web

With AUREN and `tunnel-client` running:

1. Open ChatGPT Web and go to Plugins.
2. Select the plus button, then **Add custom MCP server**.
3. Under Connection, choose **Tunnel**.
4. Select the available tunnel or paste the exact tunnel ID.
5. Leave the MCP server authentication unset if the ChatGPT connection flow offers that choice. The tunnel client injects AUREN's local bearer token on the laptop-to-MCP hop; do not configure a second OAuth provider for this local tunnel path.
6. Review and acknowledge the risk warning, create the app and inspect the discovered tool list.
7. Start with read-only tools. Enable task submission and approval decisions only for an account/workspace that supports write actions and after reviewing the AUREN scopes and approval policy.

Keep the local PowerShell process running during discovery and every ChatGPT tool call. The tunnel is unavailable after the process exits.

## Work on another project

AUREN's local MCP service is project-bound. Stop the current foreground session with Ctrl+C, then run the script again with the next repository URL or local checkout. AUREN creates or reuses its managed GitHub workspace and keeps project identity explicit; it does not silently switch a running task to another repository.

For a new GitHub repository, use the HTTPS clone URL. Private repository access must already work through the laptop's Git credential helper. For local work, pass the existing checkout path.

## Verify

While the command is running, open a second PowerShell window:

```powershell
auren verify-local --dashboard-url http://127.0.0.1:<dashboard-port>
auren chatgpt-tunnel status
```

The MCP port is 8765 unless overridden. The dashboard port is selected by the operating system and is printed in the AUREN startup output. The tunnel client exposes its own local health UI; use `tunnel-client doctor --config "$HOME/.auren/mcp/chatgpt-tunnel.yaml" --explain` if connection readiness is unclear.

## Security boundaries and limits

- Never expose port 8765 through a router, reverse proxy or public firewall.
- Never commit `.auren` state, local MCP tokens, tunnel config, runtime keys or provider credentials.
- Use the OpenAI runtime key, not an admin key. The tunnel ID and runtime key are separate values.
- AUREN's local MCP token is supplied to tunnel-client via a `file:` reference. It is not embedded in the YAML as a literal bearer token.
- ChatGPT write actions remain subject to the workspace plan, connector permissions and AUREN's server-side Guard and approval checks.
- This setup connects one active project at a time. Stop and restart to switch repositories.
- The tunnel is private development connectivity. It does not create a public plugin or make unsupported ChatGPT plans eligible.
- AUREN's local runtime still requires the configured Vibe Coding host/provider path for actual repository edits. MCP task submission alone is not proof that a coding task can execute; check `auren doctor` and the Vibe Coding host readiness report.

## Official references

- [OpenAI Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [OpenAI tunnel-client onboarding](https://github.com/openai/tunnel-client/blob/master/docs/onboarding.md)
- [OpenAI tunnel-client configuration](https://github.com/openai/tunnel-client/blob/master/docs/configuration.md)
- [ChatGPT custom MCP connection requirements](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)
