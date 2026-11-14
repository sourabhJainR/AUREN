# AUREN plug-and-play integrations

This is the entry point for installing AUREN, connecting AI hosts, selecting model providers, and adding optional capabilities. AUREN uses one canonical runtime and task authority. An integration connects to that runtime; it must not create a second task store, approval system, Guard, or memory authority.

## Choose the integration you need

AUREN has four distinct integration layers. They solve different problems and are not interchangeable.

| Layer | What it connects | AUREN path |
|---|---|---|
| AI host | Claude Code, Codex, Gemini CLI, Cursor, VS Code, ChatGPT and other clients | Agent Skills, repository instructions, MCP, or CLI |
| Model provider | OpenAI, Anthropic, Gemini, Groq, xAI and other inference APIs | Hybrid model router or explicitly injected crew provider |
| Orchestration framework | Agents SDK, AutoGen, Pydantic AI, LangGraph, n8n and similar systems | AUREN operator MCP API |
| Optional capability | Skills, MCP tools, code intelligence, retrieval and workflow packs | Capability discovery and explicit adapters |

Connecting a host does not give AUREN access to that host's private model credentials. Configuring a model API does not install an IDE plugin or connect an MCP client.

## Fastest local setup

Install the latest stable AUREN release using the [download page](https://sourabhJainR.github.io/AUREN/downloads.html), then open the repository you want AUREN to work with.

Linux/macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/sourabhJainR/AUREN/main/install.sh | bash
cd /path/to/your/repository
auren up
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/sourabhJainR/AUREN/main/install.ps1 | iex
Set-Location C:\path\to\your\repository
auren up
```

From a source checkout, use `python auren_cli.py up --project-root .`.

The first-run path validates or creates the minimal project profile, starts the shared local runtime, dashboard and loopback MCP endpoint, and configures supported local clients with backups. It preserves unrelated client settings. Use these commands to inspect or control setup:

```bash
auren doctor --project-root .
auren providers --project-root .
auren plugin connect                 # preview detected client changes
auren plugin connect --apply         # apply backed-up client changes
auren up --no-connect                # leave client settings untouched
auren up --no-browser                # headless workstation
```

Keep the local AUREN process running while local MCP clients use it. A configured API key, detected executable or successful MCP handshake is not proof of live model quality or production acceptance.

### Important command-name distinction

`auren plugin install` is a convenience alias for starting the local AUREN runtime and configuring supported local MCP clients. It does **not** install the Claude Code marketplace plugin.

To install the native Claude Code plugin, use the Claude Code commands below. Other clients use their supported Agent Skills, MCP or repository-instruction mechanisms.

## Connect an AI coding host

### Claude Code

AUREN publishes a native Claude Code plugin and an Agent Skill:

```text
/plugin marketplace add sourabhJainR/AUREN
/plugin install adaptive-ai-coding-orchestrator@adaptive-ai-engineering
```

For a local source checkout, use `/plugin marketplace add .` from the repository root. The plugin manifest is under `.claude-plugin/plugin.json`. The marketplace package contains the skill and prompt hook; it does not launch a second MCP runtime. Run `auren up` in the target repository to configure Claude Code's local MCP entry through the authenticated stdio-to-HTTP bridge. For hosted Claude, configure the separately deployed HTTPS MCP endpoint and OAuth. These are complementary paths: the skill provides workflow guidance; MCP exposes governed AUREN operations.

### Codex

Use `AGENTS.md`, the shared Agent Skill, and the AUREN MCP server. The local setup can configure the supported Codex MCP entry. For a deployed remote endpoint:

```bash
codex mcp add auren --url https://<your-auren-host>/mcp
codex mcp login auren
codex mcp list
```

AUREN does not publish a separate Codex marketplace plugin. The native connection is MCP plus Agent Skills and repository instructions.

### Gemini CLI

Use `GEMINI.md`, `AGENTS.md`, Agent Skills and MCP:

```bash
gemini skills install https://github.com/sourabhJainR/AUREN.git --path skills/ai-coding-orchestrator
gemini mcp add --transport http auren https://<your-auren-host>/mcp
gemini mcp list
```

### Cursor, VS Code and Visual Studio

Use the matching MCP configuration template under `integrations/mcp/`, plus repository instructions and the shared Agent Skill where supported:

- Cursor: `cursor.mcp.json.example`
- VS Code / GitHub Copilot: `vscode.mcp.json.example`
- Visual Studio: `visual-studio.mcp.json.example`
- GitHub Copilot CLI: `copilot-cli.mcp-config.json.example`

Open the host's tools panel and verify AUREN tool discovery. Keep host-side auto-approval disabled for consequential operations.

### ChatGPT web and Claude web

Hosted chat clients cannot reach a workstation's loopback URL. Deploy the AUREN remote Streamable HTTP MCP service behind HTTPS and configure an external OAuth authorization server first. Then add the remote MCP endpoint through the host's custom connector/app flow.

The remote MCP implementation and setup documentation exist in the repository. A hosted endpoint, OAuth tenant, and live vendor acceptance are separate deployment tasks; do not treat local MCP success as proof that hosted chat integration is ready.

### Other MCP clients

Safe templates are available for Cline, Roo Code, Continue and Zed. See [MCP Host Compatibility](MCP_HOST_COMPATIBILITY.md) for exact host-specific setup, version caveats and acceptance status.

## Connect a model provider

AUREN supports direct inference adapters for:

- OpenAI
- Anthropic
- Gemini
- Groq
- xAI
- OpenRouter
- Together AI
- Fireworks
- DeepSeek
- Mistral
- Cerebras
- Perplexity
- Cohere
- Explicitly allowlisted custom OpenAI-compatible endpoints

A local Ollama-compatible endpoint is also supported for local inference discovery and the local proposal path.

Configure provider credentials through a protected environment or secret manager. Set `AUREN_REMOTE_PROVIDER_ORDER` to an ordered, bounded fallback list and set the matching provider key/model variables. See [Multi-LLM and AI Infrastructure Quickstart](MULTI_PROVIDER_QUICKSTART.md) for Linux, macOS and PowerShell examples.

Important runtime limits:

- Remote inference is disabled by default and requires explicit public-data classification and opt-in.
- Private and restricted tasks must remain local.
- Provider fallback is sequential and bounded; automatic model voting/ensembles are not implemented.
- The direct API adapters are integrated with the hybrid model router. Graph-team roles can now use versioned provider profiles and a supervised registration helper. The host still supplies the canonical controller and approved read-only tool registry; configuring an API key alone never wires a role or grants a tool.
- API adapters return model responses; they do not execute arbitrary provider tool calls.

## Connect an orchestration framework

AUREN can act as a governed MCP operator service for external orchestration systems. Examples are maintained in [Orchestrator Integrations](ORCHESTRATOR_INTEGRATIONS.md).

| Framework | Integration path | Repository status |
|---|---|---|
| OpenAI Agents SDK | Streamable HTTP MCP client | Example documented; live acceptance pending |
| AutoGen | MCP workbench/client | Example documented; live acceptance pending |
| Pydantic AI | MCPToolset | Example documented; live acceptance pending |
| LangChain / LangGraph | MCP adapter | Setup documented; pinned sample acceptance pending |
| n8n | MCP Client Tool node | Workflow documented; live acceptance pending |
| CrewAI | MCP server attached to selected agents | Example documented; live acceptance pending |
| Semantic Kernel / Microsoft Agent Framework | MCP plugin/tool integration | Example documented; live acceptance pending |
| Haystack | MCPTool / MCPToolset | Setup documented; live auth acceptance pending |
| Flowise | Custom MCP client node | Setup documented; live auth acceptance pending |
| Dify | Dedicated plugin or narrow API facade | Native MCP path not verified |

Give each workload its own identity and minimum required scopes. Start with read-only status/evidence tools. Enable task submission only after testing idempotency, approvals and cancellation. A submit receipt is not task completion; poll the task and retrieve its evidence.

## What is plug-and-play today?

| Capability | Current state |
|---|---|
| One-command local runtime start | Implemented through `auren up` |
| Local MCP server and CLI/dashboard task path | Implemented; same canonical task controller |
| Automatic configuration of supported local clients | Implemented with backups; per-client host acceptance still required |
| Native Claude Code plugin | Packaged and documented |
| Agent Skill reuse across compatible hosts | Implemented through standard skill packaging and host-specific instructions |
| Remote MCP operator endpoint | Implemented in repository; hosted deployment and external OAuth remain operator work |
| Direct inference APIs | Bounded adapters integrated with hybrid model routing |
| Unified integration inventory | Implemented through `auren integrations`; authentication and live acceptance remain separate |
| Graph-team provider profiles | Versioned profile and atomic host registration helper implemented; production model acceptance remains separate |
| Local Ollama-compatible model discovery | Implemented; model quality is not inferred from discovery |
| Profile-driven graph-team registration | Implemented as an explicit supervised bootstrap; host-owned controller and tool registry remain required |
| Universal executable plugin loader | Not implemented; extension manifests are data-only and runtime adapters are explicit |
| Live acceptance across all listed vendors | Not completed; see the compatibility matrix |

AUREN's extension registry deliberately describes capabilities without importing or executing arbitrary plugin code. This avoids treating a downloaded manifest, a plugin's self-declared trust label, or a discovered MCP tool as permission. Installation, trust, policy approval and activation remain separate decisions.

## Harness² comparison and AUREN's implementation

Harness² identifies eight editable parts around a model. AUREN has corresponding concepts, but some are implemented through stronger runtime contracts rather than a generic plugin loader.

| Harness² component | AUREN equivalent | Assessment |
|---|---|---|
| System prompt | `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, persona and task instructions | Available across host surfaces |
| Guardrails | Canonical Guard, role/action scope, approvals and execution policy | Stronger enforcement model; production host acceptance remains separate |
| Memory | Scoped persistent memory, task memory, evidence graph and durable progress | Implemented foundations; cross-domain freshness and conflict handling continue |
| Tool guidance | MCP tool schemas, capability metadata, approved tool scopes | Implemented adapter paths; host-specific tool acceptance remains |
| Subagents | StateGraph, graph/crew runtime and software-team personas | Implemented; provider registration remains explicit |
| Skills | Standard Agent Skills with host-specific packaging | Implemented |
| Scripts | Allowlisted subprocess and provider adapters | Explicit and bounded; not arbitrary script execution |
| Plugins | Native Claude Code plugin, MCP integrations and data-only extension contract | Partial; no universal executable plugin runtime |

Harness² also demonstrates task-level Propose–Probe–Compose and persistent harness learning. AUREN has an AUREN-native bounded Propose–Probe–Compare–Compose experiment controller with manifest digests, shared execution budgets, baseline/candidate probes, separate evaluator receipts, isolation attestation checks, atomic adoption and rollback. The controller is not itself a sandbox.

The remaining Harness²-aligned work is tracked in [issue #692](https://github.com/sourabhJainR/AUREN/issues/692): production isolation/evaluator trust roots, resumable experiment recovery, explicit parallel and sequential exploration, durable provenance-aware learning, component-specific change policy, transfer/holdout evaluation and dashboard integration. Do not describe repository test fixtures as independent isolation or evaluation evidence.

## Additional improvements worth prioritising

The first integration inventory and role-profile setup are now implemented. Remaining work:

1. **Versioned integration packs:** signed/pinned manifests, compatibility ranges, provenance, permission diffs, rollback and a clear install/enable lifecycle. Keep execution in explicit adapters or isolated processes.
2. **Live host acceptance harness:** smoke-test tool discovery, OAuth refresh/revocation, identity scopes, task submission, cancellation, restart and evidence retrieval for each supported client/version.
3. **Harness experiment operations:** complete resume, parallel/sequential modes, experiment-tree UI, per-candidate cost/quality/safety reporting and independent evaluator custody.
4. **Provider observability:** record model/provider version, latency, token usage, cost, route reason, fallback and task outcome without logging secrets or sensitive prompts.

## Validation and readiness

Run the repository checks from the AUREN root:

```bash
python auren_cli.py doctor --project-root .
python auren_cli.py providers --project-root .
python -m unittest tests.test_extension_contract tests.test_agent_capabilities -v
python -m unittest tests.test_harness_experiments tests.test_harness_isolation_verifier -v
```

For a live host, also record its exact product/version, MCP protocol version, OAuth issuer, scoped tool-list digest, test identity, acceptance result and sanitized evidence reference. Until that receipt exists, mark the integration as documented or configured—not live verified.

## Related documentation

- [Provider Role Profiles](PROVIDER_ROLE_PROFILES.md)
- [Multi-LLM and AI Infrastructure Quickstart](MULTI_PROVIDER_QUICKSTART.md)
- [MCP Host Compatibility](MCP_HOST_COMPATIBILITY.md)
- [Platform Compatibility Matrix](PLATFORM_COMPATIBILITY_MATRIX.md)
- [Orchestrator Integrations](ORCHESTRATOR_INTEGRATIONS.md)
- [Extension Contract](EXTENSION_CONTRACT.md)
- [Capability-aware Execution](CAPABILITY_AWARE_EXECUTION.md)
- [Harness Experiment Controller](HARNESS_EXPERIMENT_CONTROLLER.md)
- [Standalone Readiness and Gap Matrix](STANDALONE_READINESS_AND_GAP_MATRIX.md)
