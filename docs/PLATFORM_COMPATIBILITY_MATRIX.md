# AUREN Cross-Platform Compatibility and Integration Gaps

**Review date:** 2026-11-09  
**Scope:** ChatGPT web and desktop, Codex app and CLI, Claude chat and Claude Code surfaces, Gemini, Groq, xAI/Grok, Cursor, Visual Studio / VS Code, and GitHub Copilot.

## How to read this review

The status below describes repository artifacts and integration paths found in AUREN. It is not a claim that every vendor client was installed and exercised. No live vendor credentials or local desktop applications were available for this audit.

- **Repository configured:** AUREN has the expected native instruction, skill, plugin or CLI integration artifacts. A live host acceptance run is still required.
- **Partial:** Some native integration exists, but an adapter, distribution package, or host-specific setup is missing.
- **Chat only:** Useful for discussion and supplied context, not direct local repository execution.
- **Not wired:** The vendor exposes a usable API or tool protocol, but AUREN has no production provider adapter for it.
- **Not native:** There is no dedicated AUREN integration for that product surface.

## Executive summary

AUREN already has a strong cross-host foundation: `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, the shared Agent Skill under `.agents/skills` and `skills/`, a Claude plugin, a guarded provider CLI bridge, and a local stdio MCP entry point.

The main gap is not the engineering workflow. It is the last-mile product integration:

1. AUREN now has an authenticated remote Streamable HTTP MCP service and a broader operator tool surface. It advertises MCP 2026-07-28 stateless mode and retains the 2025 handshake for older hosts. It is implemented in the repository but not yet deployed or live-tested with vendor clients.
2. The service uses an external OAuth authorization server for hosted-client login. AUREN validates issuer, audience, expiry and scopes through token introspection; it does not implement the authorization server, PKCE, DCR or CIMD itself.
3. Codex, Claude, Gemini, Cursor and VS Code remote MCP setup paths are documented. Client-specific OAuth and desktop/IDE acceptance remains open.
4. Claude web and Claude Desktop support remote MCP connectors; this does not give those chat surfaces local filesystem access. AUREN executes tasks on its configured host.
5. Cursor now has a project rule and remote MCP setup guide. Antigravity CLI/IDE and Visual Studio 2022 17.14+/2026 are also documented as remote MCP hosts; none has passed live AUREN acceptance yet.
6. Direct inference adapters now cover Anthropic, OpenAI, Gemini, Groq, xAI, OpenRouter, Together, Fireworks, DeepSeek, Mistral, Cerebras, Perplexity, Cohere and allowlisted custom OpenAI-compatible endpoints. Live vendor acceptance remains open.
7. The compatibility harness is static by default. Live checks for installed vendor clients and desktop/IDE workflows remain opt-in or manual.

## Compatibility matrix

| Product surface | Status | Available AUREN path | Main gap |
|---|---|---|---|
| ChatGPT web | Partial | Remote Streamable HTTP MCP, OAuth tool metadata, profile identity | External OAuth 2.1 authorization server, ChatGPT app setup and live acceptance |
| ChatGPT desktop app | Repository configured | Settings → MCP servers; remote Streamable HTTP or stdio; OAuth; shared Codex host configuration | Live AUREN OAuth, tool discovery and long-running task acceptance remain unverified |
| Codex app / IDE extension | Partial | Shared Codex MCP configuration, `AGENTS.md`, Agent Skills, remote MCP | Live OAuth and tool acceptance not recorded |
| Codex CLI | Partial | `AGENTS.md`, skills, remote MCP, local `~/.codex/config.toml` auto-configuration through `auren up`, `codex exec` | Live OAuth, tool and local stdio-proxy acceptance not recorded |
| Codex app | Repository configured | Shared Codex MCP configuration and skills | App-specific live acceptance not recorded |
| Claude chat web | Partial | Remote MCP custom connectors, OAuth | AUREN endpoint deployment and Claude connector acceptance |
| Claude Code CLI | Repository configured | `CLAUDE.md`, skill, plugin, hooks, remote MCP | Live provider and permission-mode checks |
| Claude Desktop | Partial | Remote MCP Connectors and Claude Code Code tab | Connector/OAuth acceptance not recorded; remote tools execute on AUREN host |
| Claude Code for VS Code | Repository configured | `CLAUDE.md`, skill, plugin and remote MCP | VS Code extension smoke test not in CI |
| Microsoft Visual Studio IDE | Repository configured | Visual Studio 2022 17.14+/2026 `.mcp.json`, GitHub Copilot MCP host, remote Streamable HTTP | Live AUREN OAuth, tool approval and task lifecycle acceptance remain open |
| Gemini CLI | Repository configured | `GEMINI.md`, `AGENTS.md`, skill, remote HTTP MCP, headless mode | OAuth and host smoke test not recorded; some consumer plans have moved to Antigravity |
| Gemini Code Assist in VS Code | Partial | Agent mode can use MCP through Gemini CLI settings | AUREN connection not tested; product/plan-specific migration applies |
| Cursor | Partial | `AGENTS.md`, skills, `.cursor/rules/auren.mdc`, remote MCP | OAuth and local/cloud/SSH acceptance still open |
| Groq API | Partial | Native AUREN Groq Chat Completions adapter with supervised failover | Live credentials, current model IDs and cost/latency conformance remain unverified |
| Grok chat web | Chat only | Supplied context only | No AUREN repository execution surface |
| xAI API / Grok API | Partial | Native AUREN xAI Responses API adapter with supervised failover | Live credentials, current model IDs and xAI-specific feature conformance remain unverified |
| Grok Build CLI | Partial | Remote MCP is available upstream | No AUREN-specific local CLI/skill package |
| GitHub Copilot | Repository configured | `AGENTS.md`, `.github/copilot-instructions.md`, skills, MCP | Copilot Agent-mode acceptance not in CI |
| OpenAI API | Partial | Native Responses API adapter; supervised failover | Live credentials and model-ID acceptance remain unverified |
| Anthropic API | Partial | Native Messages API adapter; supervised failover | Live credentials and model-ID acceptance remain unverified |
| Gemini API | Partial | OpenAI-compatible Chat Completions adapter | Live credentials and Google-specific feature conformance remain unverified |
| OpenRouter API | Partial | Native OpenAI-compatible adapter | Live credentials, provider routing and pricing remain unverified |
| Together API | Partial | Native OpenAI-compatible adapter | Live credentials and model-ID acceptance remain unverified |
| Fireworks API | Partial | Native OpenAI-compatible adapter | Live credentials and model-ID acceptance remain unverified |
| DeepSeek API | Partial | Native OpenAI-compatible adapter | Live credentials and model-ID acceptance remain unverified |
| Mistral API | Partial | Native OpenAI-compatible adapter | Live credentials and model-ID acceptance remain unverified |
| Cerebras API | Partial | Native OpenAI-compatible adapter | Live credentials and model-ID acceptance remain unverified |
| Perplexity API | Partial | Native OpenAI-compatible adapter | Live credentials and model-ID acceptance remain unverified |
| Cohere API | Partial | Native Cohere Chat API adapter | Live credentials and Cohere-specific feature conformance remain unverified |
| Custom OpenAI-compatible API | Partial | Generic adapter with exact HTTPS hostname allowlist | Provider-specific behavior and live conformance remain unverified |
| Cline IDE / CLI | Partial | Remote Streamable HTTP MCP and OAuth | No AUREN live host acceptance |
| Roo Code | Partial | Project/global MCP configuration | No AUREN live host acceptance |
| Continue | Partial | MCP in Agent mode; remote Streamable HTTP | No AUREN live host acceptance |
| Zed Agent | Partial | Remote MCP and OAuth; ACP external agents | No AUREN live host acceptance |
| OpenHands local | Partial | Local Streamable HTTP MCP | Cloud support must not be assumed; no AUREN acceptance |
| Windsurf | Partial | Local `~/.codeium/windsurf/mcp_config.json` auto-configuration plus remote MCP | Version-specific config and live acceptance remain unverified |
| Devin Local | Partial | Product-version-specific MCP configuration | Product configuration changed; no live acceptance |

The machine-readable declarations live in `.ai-harness/PROVIDER_MATRIX.json`. The values there distinguish product integration from model-provider support.

For local workstation setup, `auren up` starts the shared local MCP and desktop console and configures detected supported JSON hosts plus Codex TOML. Hosted chat surfaces still require the separate HTTPS remote MCP deployment and OAuth path; AUREN does not send a loopback endpoint to a cloud client or treat a vendor chat login as an API key.

See [MCP Host Compatibility](MCP_HOST_COMPATIBILITY.md) for host-specific setup examples and a per-client acceptance checklist, [Orchestrator Integrations](ORCHESTRATOR_INTEGRATIONS.md) for OpenAI Agents SDK, AutoGen, Pydantic AI, LangChain/LangGraph, n8n, CrewAI, Semantic Kernel, Haystack, Flowise and OpenHands, and [Model Provider Integration](MODEL_PROVIDER_INTEGRATION.md) for direct API configuration, per-request provider choice and privacy boundaries.

## Agent orchestration connectors

AUREN's remote Streamable HTTP MCP endpoint is the common integration contract for orchestration frameworks. First-party setup examples are in [Orchestrator Integrations](ORCHESTRATOR_INTEGRATIONS.md).

| Framework | Connection path | Repository state | Remaining proof |
|---|---|---|---|
| OpenAI Agents SDK | `MCPServerStreamableHttp` / hosted MCP | Example documented | OAuth client and live tool acceptance |
| Microsoft AutoGen | `StreamableHttpServerParams` and `mcp_server_tools` | Example documented | Live task lifecycle and approval acceptance |
| Pydantic AI | `MCPToolset` with per-run auth | Example documented | Live per-user session and tool acceptance |
| LangChain / LangGraph | `langchain-mcp-adapters` | Adapter path documented | Version-pinned sample and live graph acceptance |
| n8n | MCP Client Tool node | Setup documented | Live workflow acceptance and credential-scope validation |
| Semantic Kernel / Microsoft Agent Framework | Remote MCP plugin/tool integration | Example documented | Pin package line and validate OAuth, lifecycle and permissions |
| Haystack | `mcp-haystack` Streamable HTTP toolset | Example documented | Verify bearer/OAuth support in pinned integration version |
| Flowise | Custom MCP client node / Agentflow | Setup documented | Validate OAuth credential storage and approval handling |
| OpenHands local | Local or remote SHTTP MCP | Partial | Verify AUREN OAuth; OpenHands Cloud path is not assumed |
| Dify | Plugin/API extension, not verified native MCP client | Not native | Dedicated Dify plugin and tenant credential/approval validation |
| OpenHands local | Local MCP configuration | Partial | Version-specific configuration and acceptance |
| CrewAI | Remote Streamable HTTP `MCPServerHTTP` agent integration | Example documented | Pin SDK and validate AUREN OAuth, tool filtering and task lifecycle |

The framework examples use AUREN's remote endpoint, not a separate framework-specific execution engine. The authenticated principal, workspace binding, idempotency, approval and evidence contracts remain on the AUREN side. A framework's model provider remains independently selectable.

## Direct model API adapters

AUREN includes fixed-host, bounded API adapters for Anthropic Messages, OpenAI Responses, xAI Responses and OpenAI-compatible Chat Completions providers: Gemini, Groq, OpenRouter, Together, Fireworks, DeepSeek, Mistral, Cerebras, Perplexity and Cohere. They use guarded HTTPS, explicit model IDs, per-provider credentials and ordered failover charged against the existing supervisor budget.

Configure `AUREN_REMOTE_PROVIDER_ORDER` to choose provider order, and set each selected provider's API key and model ID through the deployment secret/environment configuration. For example, Groq uses `GROQ_API_KEY` and `GROQ_MODEL`; xAI uses `XAI_API_KEY` and `XAI_MODEL`. A custom OpenAI-compatible endpoint requires `AUREN_CUSTOM_PROVIDER_URL`, `AUREN_CUSTOM_PROVIDER_ALLOWED_HOSTS`, `AUREN_CUSTOM_PROVIDER_API_KEY` and `AUREN_CUSTOM_PROVIDER_MODEL`. The adapter does not stream, discover model catalogs, calculate current prices, or execute model-returned tool calls. Live vendor credential acceptance remains unverified. Remote inference is opt-in and only eligible for tasks classified as public or non-sensitive; private and restricted tasks remain local-only.

MCP host connectivity and direct model inference are separate integration layers. A ChatGPT, Claude, Gemini or IDE client connecting to AUREN over MCP does not give AUREN access to that host's model credentials or inference API.


## Important product distinctions

### ChatGPT web is not Codex CLI

ChatGPT web uses remote MCP tools through installed MCP-backed plugins; package and publish AUREN through Plugin Creator/Sites before users can install it. It connects to a remote MCP endpoint and cannot launch AUREN's local stdio process. Full MCP write/modify support depends on workspace, plan and admin permissions; verify current eligibility at deployment time. ChatGPT desktop has a separate Settings → MCP servers flow and shares the Codex host configuration with Codex CLI and the IDE extension. Do not treat web plugin installation and desktop/Codex MCP registration as the same setup path.

**Required AUREN work:**
- Deploy a remote Streamable HTTP MCP service or a supported Secure MCP Tunnel bridge.
- Add authentication, tenant/user identity, per-tool authorization, rate limits, audit receipts and explicit approval for consequential writes.
- Expose only narrow tools. Start with status, task submission, task status and evidence retrieval; add write operations only after authorization and idempotency tests.
- Package the Agent Skill and app as a ChatGPT plugin if distribution in Chat and Work is required.

### Codex app and Codex CLI

Both can consume repository guidance, Agent Skills and MCP. AUREN already has `AGENTS.md`, a project skill under `.agents/skills`, and a CLI provider command. ChatGPT's universal plugin distribution is a separate step; the presence of a repository skill does not mean AUREN is published in the plugin directory.

**Required AUREN work:**
- Verify skill discovery in the installed Codex app and CLI.
- Register the AUREN MCP server in Codex settings and verify the tool list.
- Add a disposable-repository acceptance test that proves sandbox mode, cancellation, evidence capture and exact-head checks are preserved.

### Claude chat versus Claude Code

Ordinary Claude chat (web or desktop chat) is not the same product surface as Claude Code. Claude Code can operate on a repository through the CLI, the VS Code extension, and the Code experience in Claude Desktop. AUREN already packages a Claude plugin and skill.

**Required AUREN work:**
- Keep the normal chat path informational unless a remote, authenticated MCP connector is deliberately configured.
- Test the plugin's MCP process startup, skill discovery, hook execution, tool schema and permission behavior against current Claude Code versions.
- Treat the VS Code extension as a Claude Code host. Do not claim a native integration with Microsoft Visual Studio itself.

### Gemini CLI versus Gemini Code Assist

Gemini CLI has native support for Agent Skills, MCP and headless JSON/JSONL output. AUREN has a Gemini instruction file, shared skill and a provider command. Gemini CLI's IDE companion is distinct from Gemini Code Assist; configuration for one must not be assumed to work in the other.

**Required AUREN work:**
- Use `portable/chat_capability.py` only with `AUREN_MCP_RUNTIME_FACTORY=trusted_module:create_runtime` and `AUREN_PROJECT_ROOT=/absolute/workspace`; the bridge rejects missing factories and empty graphs.
- Verify `/skills list`, `/mcp list`, and a headless JSONL request.
- Add a Gemini-specific parser for structured output and failure/turn-limit exit codes.
- Test Gemini Code Assist separately before listing it as fully supported.

### Cursor

Cursor supports `AGENTS.md`, project rules, Agent Skills and MCP. AUREN's shared `.agents/skills` directory is the right portable skill location. Cursor's local user skills are not automatically copied to Cloud Agents or remote workers; keep the AUREN skill in the repository for those environments.

**Required AUREN work:**
- Use the new `.cursor/rules/auren.mdc` rule as a short pointer to `AGENTS.md` and the AUREN skill.
- Add the local MCP server through Cursor's MCP settings or project config using `portable/chat_capability.py`, with a trusted runtime factory and bound `AUREN_PROJECT_ROOT` in the process environment.
- Validate local Agent, Cloud Agent, remote SSH and self-hosted worker paths independently.

### Groq, xAI/Grok and other model APIs

These are inference backends, not repository execution hosts. AUREN now has native API adapters for Groq and xAI alongside Anthropic, OpenAI, Gemini, OpenRouter, Together, Fireworks, DeepSeek, Mistral, Cerebras, Perplexity and Cohere. A custom OpenAI-compatible endpoint can be added only with an explicit HTTPS hostname allowlist.

The adapters:
- Use guarded HTTPS with exact host allowlists for built-in vendors and explicit host allowlisting for custom endpoints.
- Read credentials from provider-specific process environment variables; keys are not forwarded through a broad child-process environment.
- Require explicit model IDs and provider order through `AUREN_REMOTE_PROVIDER_ORDER`.
- Charge each provider attempt against the existing supervisor budget.
- Keep private and restricted tasks local-only.
- Normalize usage and tool-call metadata without executing model-returned tool calls.

The remaining work is live vendor conformance, model-version checks, rate-limit/outage tests, price and latency measurement, provider retention/residency review and paired quality/safety evaluation. Streaming, automatic model discovery and cost-price calculation are not implemented in this adapter slice.

OpenAI-compatible endpoints do not share identical semantics. Provider-specific tool calling, structured output, reasoning fields, streaming and approval behavior must be validated separately. AUREN's own Guard and approval checks remain authoritative; provider-side MCP approval flags never replace them.

## MCP hosts versus model providers

These are two separate compatibility layers.

- **MCP host compatibility:** ChatGPT, Codex, Claude, Gemini/Antigravity, Cursor, VS Code Copilot and Visual Studio can connect through the remote MCP endpoint when their installed version supports Streamable HTTP and the configured OAuth flow.
- **Model-provider compatibility:** ProviderFabric discovers Claude, Codex, Gemini and Copilot CLI hosts. HybridModelRouter can also use the direct HTTP provider pool when an explicit provider order is configured. Local fallback uses an Ollama-compatible loopback endpoint.
- **Direct API boundary:** Built-in and custom adapters cover a broad set of hosted APIs, but they do not implement every vendor-specific feature, streaming or cost catalog. Live credential validation is not yet recorded.
- **Privacy boundary:** The hybrid router forbids remote fallback for private and restricted tasks. Provider keys stay in process environment/secret injection, endpoints are allowlisted, tool calls are normalized but not executed, and each provider attempt consumes the finite supervisor budget.

AUREN can serve as the MCP execution layer for multiple model hosts and can route eligible inference tasks through a configured provider chain. Those are separate integration layers with separate acceptance evidence.

## Current MCP boundary

The local stdio server remains `portable/chat_capability.py` and requires a trusted runtime factory and a bound workspace. It is intended for local MCP hosts. The remote operator service implementation in this review branch is:

- `portable/remote_mcp.py` — Streamable HTTP ASGI transport, OAuth resource-server validation, scoped tool catalog, task/evidence/approval operations, durable operator admission state and readiness.
- `portable/remote_mcp_server.py` — ASGI runner with a trusted runtime-factory requirement.
- `docs/REMOTE_MCP_OPERATIONS.md` — deployment and client setup instructions.
- `tests/test_remote_mcp_service.py` — protocol, auth, scope, profile, cancellation, admission and readiness tests.

The remote service exposes task submission, status, listing, cancellation, evidence, approval listing/decision, blocked-provider reconciliation, service status and admission pause/resume. It binds the workspace root on the server. Tool schemas are validated server-side. It does not expose a general shell or arbitrary file/network tool.

ChatGPT integration includes per-tool OAuth securitySchemes, a stable profile tool, protected-resource metadata and the tool-level authentication challenge required by ChatGPT's connector flow. AUREN remains the OAuth resource server: an external identity provider must implement authorization-code PKCE S256, resource/audience propagation, issuer discovery and CIMD or DCR. The external provider and ChatGPT connection have not yet been configured or live-tested.

The service currently uses AUREN's SQLite-backed durable stores and is a single-host deployment. Do not deploy multiple physical-host replicas against SQLite or claim high availability. Multi-host operation remains gated on the deployment PostgreSQL store and independent host/failover evidence.

`portable/agency_mcp.py` contains an MCP tool-endpoint abstraction; it is not the executable stdio server. Do not use it as the command in a host MCP configuration.

## Prioritized implementation plan

| Priority | Work item | Acceptance evidence |
|---|---|---|
| P0 | Correct all MCP setup instructions to use `portable/chat_capability.py` | Docs test plus local stdio initialize/list/call smoke tests |
| P0 | Deploy remote MCP service with an external OAuth 2.1 provider | PKCE S256, resource/audience, issuer, DCR/CIMD and live ChatGPT/Claude OAuth evidence |
| P0 | Complete remote operator tool authorization and independent review | Authenticated task, evidence, approval and cancellation acceptance; red-team results |
| P0 | Replace SQLite control/trigger state for multi-host operation | PostgreSQL-backed admission, task and trigger stores with physical-host/failover evidence |
| P1 | Add Cursor rule and host configuration guide | Rule/skill discovery and local/cloud/SSH acceptance results |
| P1 | Add Codex app and CLI acceptance matrix | Read-only and guarded-write smoke tests, exact-head CI evidence |
| P1 | Add Claude CLI/Desktop/VS Code host acceptance | Plugin, hook, skill, MCP and permission-mode test receipts |
| P1 | Add Gemini headless and IDE acceptance | JSONL parser tests, MCP and skill discovery receipts |
| P1 | Run live provider acceptance for configured API vendors | AUREN now has an explicit one-request synthetic API probe with latency, usage and digest evidence; run it per provider. Vendor-specific rate-limit, failover, privacy and production acceptance remain open |
| P1 | Add provider cost and latency accounting | Route traces include provider latency and token usage; exact-model estimates are available with a reviewed, versioned local price catalog. Invoice reconciliation, cache tiers and local-model cost accounting remain open |
| P2 | Add streaming and provider-specific tool/structured-output conformance | Versioned contract tests for each provider and model family |
| P2 | Add GitHub Copilot and Gemini Code Assist host acceptance | Product-specific smoke tests; no inference from shared file support |
| P2 | Add CI environment with vendor CLI installations where licensing permits | Version-pinned, non-mutating provider probes and published evidence |

## Verification commands

From an AUREN checkout:

```bash
python scripts/provider_conformance.py --json
python scripts/provider_conformance.py --live-api --api-provider openai --json
python -m unittest tests.test_provider_conformance -v
python -m unittest tests.test_ai_coding_system_practices -v
python -m unittest discover -s tests -p '*mcp*' -v
python -m unittest tests.test_remote_mcp_service -v
```

The static suite checks repository declarations only. The `--live-api` probe sends one fixed synthetic public prompt to each explicitly selected, allowlisted direct API provider; it requires `AUREN_LOCAL_REMOTE_INFERENCE=enabled` and may incur provider charges. It does not test IDE/MCP host acceptance or production privacy terms. CLI host probes remain separately enabled with `--live`.

## Official product references

- [Codex customization: AGENTS.md, skills, MCP and subagents](https://learn.chatgpt.com/docs/customization/overview)
- [ChatGPT developer mode and MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt)
- [ChatGPT custom MCP server connections](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)
- [ChatGPT and Codex plugins](https://help.openai.com/en/articles/20001256-plugins-in-chatgpt)
- [Claude custom remote MCP connectors](https://support.anthropic.com/en/articles/11503834-building-custom-integrations-via-remote-mcp)
- [Claude and Claude Desktop remote connectors](https://support.anthropic.com/en/articles/11175166-about-custom-integrations-using-remote-mcp)
- [ChatGPT custom MCP connections](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)
- [OpenAI MCP authentication](https://developers.openai.com/plugins/build/auth)
- [OpenAI MCP integration guide](https://developers.openai.com/api/docs/mcp)
- [Claude remote MCP connectors](https://support.anthropic.com/en/articles/11503834-building-custom-integrations-via-remote-mcp)
- [Gemini CLI to Antigravity transition](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/)
- [Antigravity MCP configuration](https://antigravity.google/docs/mcp)
- [Gemini CLI to Antigravity transition](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/)
- [Gemini Code Assist Agent mode](https://docs.cloud.google.com/gemini/docs/codeassist/use-agentic-chat-pair-programmer)
- [Visual Studio MCP servers](https://learn.microsoft.com/en-us/visualstudio/ide/mcp-servers?view=visualstudio)
- [Claude Code CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-usage)
- [Claude Code troubleshooting and VS Code integration](https://code.claude.com/docs/en/troubleshooting)
- [Gemini CLI Agent Skills](https://geminicli.com/docs/cli/skills/)
- [Gemini CLI MCP servers](https://geminicli.com/docs/tools/mcp-server/)
- [Gemini CLI headless mode](https://geminicli.com/docs/cli/headless/)
- [Cursor Agent Skills](https://cursor.com/docs/skills)
- [Cursor rules and AGENTS.md](https://docs.cursor.com/context/rules-for-ai)
- [Groq OpenAI API compatibility](https://console.groq.com/docs/openai)
- [Groq remote MCP tools](https://console.groq.com/docs/tool-use/remote-mcp)
- [xAI remote MCP tools](https://docs.x.ai/developers/tools/remote-mcp)
- [xAI API reference](https://docs.x.ai/developers/rest-api-reference/inference)
