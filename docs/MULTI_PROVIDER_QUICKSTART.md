# AUREN multi-LLM and AI infrastructure quickstart

For a single entry point covering local installation, coding hosts, orchestration frameworks, model APIs and extension boundaries, see [Plug-and-Play Integrations](PLUG_AND_PLAY_INTEGRATIONS.md).

AUREN supports two separate integration layers:

1. **Inference providers** supply model responses to AUREN's own router.
2. **AI hosts and orchestration frameworks** connect to AUREN through Agent Skills, MCP, CLI or a reviewed API integration.

Connecting an AI host does not give AUREN access to that host's private model credentials. Configuring an inference API does not install an IDE plugin or connect an MCP client.

## 1. Install and inspect

From an AUREN checkout:

    python -m pip install "uvicorn[standard]>=0.35,<1"
    python auren_cli.py up --project-root /absolute/path/to/project

`auren up` creates the minimal project profile only when missing, validates existing settings, reports provider readiness, starts the shared local runtime, configures supported local MCP clients with backups, and opens the Session Control Center. Leave this process running. Use `python auren_cli.py providers --project-root /absolute/path/to/project` from another terminal when you want a separate provider report; you no longer need to run `init`, `doctor`, `connect` and `local` as separate setup steps.

A detected executable or configured key/model pair is not live vendor acceptance. The report never prints API keys.

## 2. Use one or more hosted model APIs

Set the provider order in the environment inherited by the AUREN process. AUREN supports Anthropic, OpenAI, Gemini, Groq, xAI, OpenRouter, Together AI, Fireworks, DeepSeek, Mistral, Cerebras, Perplexity, Cohere and an explicitly allowlisted custom OpenAI-compatible endpoint.

Example: Groq first, OpenAI second, xAI third.

Linux/macOS:

    export AUREN_REMOTE_PROVIDER_ORDER=groq,openai,xai
    export GROQ_API_KEY="<secret-manager-injected-value>"
    export GROQ_MODEL="<model-id-enabled-for-your-account>"
    export OPENAI_API_KEY="<secret-manager-injected-value>"
    export OPENAI_MODEL="<model-id-enabled-for-your-account>"
    export XAI_API_KEY="<secret-manager-injected-value>"
    export XAI_MODEL="<model-id-enabled-for-your-account>"

Windows PowerShell:

    $env:AUREN_REMOTE_PROVIDER_ORDER = "groq,openai,xai"
    $env:GROQ_API_KEY = "<secret-manager-injected-value>"
    $env:GROQ_MODEL = "<model-id-enabled-for-your-account>"
    $env:OPENAI_API_KEY = "<secret-manager-injected-value>"
    $env:OPENAI_MODEL = "<model-id-enabled-for-your-account>"
    $env:XAI_API_KEY = "<secret-manager-injected-value>"
    $env:XAI_MODEL = "<model-id-enabled-for-your-account>"

Use exact model IDs supported by your vendor account and region. Keep credentials in a secret manager or protected process environment. Do not commit them to a repository, .env file, MCP config, task, prompt or evidence record.

The order is a **bounded fallback chain**, not parallel model execution. AUREN attempts one provider per attempt; if it fails, the next configured provider may be tried only within the same supervisor-controlled attempt and deadline budget. The current route is capped at three remote attempts. Automatic majority voting and model ensembles are not implemented.

To select a smaller ordered subset for one route, pass provider_order to the routing API, for example:

    {
      "provider_order": ["openai", "groq"]
    }

Only providers already configured by the host can be selected. Unknown, duplicate or unavailable providers are rejected. Persisted per-user provider preferences and tenant-scoped credentials are not yet implemented.

After setting the environment, restart AUREN so it inherits the new configuration, then run:

    python auren_cli.py doctor --project-root /absolute/path/to/project

Look for remote_model_providers.status = configured_not_live_validated. This confirms only that provider names, credential variables and model IDs are present. Run a vendor-specific read-only conformance probe before production use. Check account access, rate limits, model availability, latency, usage, cost, data retention, training, residency and intended-use terms.

## 3. Use a local model

Install and start Ollama or another Ollama-compatible local endpoint. Pull a model that fits your machine:

    ollama pull qwen2.5-coder:7b
    ollama list

AUREN probes http://127.0.0.1:11434 without sending a prompt. To use another loopback port:

    export AUREN_LOCAL_MODEL_URL=http://127.0.0.1:11434
    python auren_cli.py doctor --project-root /absolute/path/to/project

The local discovery report confirms endpoint and model visibility, not model quality. The built-in local MCP proposal path now prefers an admission-bound Ollama-compatible model for private work. If no capable local model is available, direct API inference is eligible only for explicitly public work after both remote-inference opt-ins are set. Provider identity is fixed at admission; AUREN does not silently switch providers after a task is accepted. Local model URLs must remain loopback-only.

## 4. Configure the local AUREN task path

The built-in local composition is proposal-only. It uses a configured local Ollama-compatible model for private work when available. Direct API providers are used only when no capable local model is available and the workspace is explicitly declared public with remote inference enabled. The provider has no repository tools and cannot modify files; this is not yet a full repository coding-agent executor.

Remote inference is disabled by default. For an explicitly public workspace only, configure both the provider and policy opt-in:

    export AUREN_REMOTE_PROVIDER_ORDER=groq,openai
    export GROQ_API_KEY="<secret-manager-injected-value>"
    export GROQ_MODEL="<model-id>"
    export OPENAI_API_KEY="<secret-manager-injected-value>"
    export OPENAI_MODEL="<model-id>"
    export AUREN_LOCAL_REMOTE_INFERENCE=enabled
    export AUREN_LOCAL_DATA_CLASSIFICATION=public
    python auren_cli.py up --project-root /absolute/path/to/project --no-browser

The service binds to loopback and prints its MCP and dashboard addresses. Use the printed MCP URL and local operator credential with a supported local MCP client. Keep private or restricted project work local; do not change classification just to enable remote inference.

The API provider chain and graph-team roles are separate configuration surfaces. Use `auren integrations profile init` to create a versioned provider/model profile for all graph-team roles, then review the per-role models and read-only tool scopes. Register it through the supervised host bootstrap helper. API keys alone do not auto-wire roles or grant tools. See [Provider Role Profiles](PROVIDER_ROLE_PROFILES.md).

## 5. Connect AI coding hosts

Install AUREN's Agent Skill and/or connect its MCP server. Keep AUREN Guard, approval, workspace and evidence checks enabled.

| Host | Recommended connection |
|---|---|
| Claude Code | AUREN Agent Skill plus local stdio/HTTP MCP; remote MCP for a hosted endpoint |
| Codex CLI / app | AGENTS.md, AUREN Agent Skill and MCP |
| Gemini CLI | Gemini Agent Skills plus MCP |
| Cursor | AGENTS.md, project rules and MCP |
| VS Code / GitHub Copilot | AGENTS.md, Copilot instructions, skills and MCP |
| Visual Studio | MCP configuration template and repository instructions |
| ChatGPT web / Claude web | Deployed HTTPS MCP with external OAuth; loopback URLs will not work |
| Cline, Roo Code, Continue, Zed | Matching safe example under integrations/mcp/ |

Templates are under integrations/mcp/. They contain placeholders only. Replace the endpoint with your deployed AUREN endpoint and complete the host's supported authentication flow. Do not paste API keys into client configuration.

## 6. Connect orchestration and automation infrastructure

AUREN exposes an operator MCP surface that frameworks can use as a governed tool server. Examples and version caveats are in [Orchestrator Integrations](ORCHESTRATOR_INTEGRATIONS.md).

| Infrastructure | Integration path |
|---|---|
| OpenAI Agents SDK | Streamable HTTP MCP server |
| AutoGen | MCP workbench/client |
| Pydantic AI | MCP server integration |
| LangChain / LangGraph | MCP adapter or tools bridge |
| n8n | MCP client node; store credentials in n8n credentials |
| CrewAI | MCP server attached to selected agents |
| Semantic Kernel / Microsoft Agent Framework | MCP plugin/tool integration |
| Haystack | MCPTool / MCPToolset |
| Flowise | Custom MCP client node |
| Dify | Plugin/custom tool path; validate transport and auth for the installed version |
| OpenHands | MCP/tool integration where supported by the deployed version |

For each framework:
1. Create a separate service identity/token for each user or workload.
2. Grant only required AUREN scopes.
3. Start with read-only status and evidence tools.
4. Add task submission only after approval and idempotency behavior is tested.
5. Poll task state and retrieve evidence; a submit receipt is not completion.
6. Keep host retries for writes disabled unless the same idempotency key is reused.
7. Do not enable blanket auto-approval.

The remote MCP server is implemented, but hosted deployment, external OAuth configuration and vendor-client acceptance remain operator gates. SQLite is single-host only; do not run a multi-host service over shared SQLite.

## 7. Verify the integration

1. Run auren doctor and inspect local, CLI and API provider status.
2. Confirm MCP handshake and tool discovery with a read-only call.
3. Confirm the authenticated identity sees only its authorized workspace and tools.
4. Submit a harmless public test task with a stable idempotency key.
5. Poll task state until terminal; fetch the evidence record.
6. Repeat with one provider unavailable and verify bounded failover.
7. Test a private/restricted task and confirm remote inference is denied.
8. Review provider privacy terms, logs and cost controls before real workloads.

## 8. Current limitations

- API provider fallback is sequential, bounded and supervisor-charged; parallel model ensembles and automatic voting are not implemented.
- Per-user saved provider preferences and tenant-scoped API credential management are not implemented.
- Graph/crew provider invokers are host-registered; configuring an API provider does not automatically wire every role.
- The built-in local MCP task handler is proposal-only and cannot edit files, run builds, merge or release.
- Local CLI discovery does not mean AUREN can safely execute that CLI. A scoped subprocess/workspace executor is required.
- Direct provider tests use mocked transports. Live provider, license, model-quality, OAuth and AI-host acceptance must be run against real accounts and deployments.
- Private/restricted prompts must not be sent to remote APIs. A model response is not approval or verification evidence.

See [Model Provider Integration](MODEL_PROVIDER_INTEGRATION.md), [Usage and Platform Integration](USAGE_AND_PLATFORM_INTEGRATION.md), [Local Self-Hosted MCP](LOCAL_SELF_HOSTED_MCP.md), [Remote MCP Operations](REMOTE_MCP_OPERATIONS.md) and [Platform Compatibility Matrix](PLATFORM_COMPATIBILITY_MATRIX.md).
