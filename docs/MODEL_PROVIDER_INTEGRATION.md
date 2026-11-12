# AUREN Model Provider Integration

AUREN separates model inference from execution authority. The model can change between tasks or attempts; the AUREN supervisor, Guard, approval checks, tool registry, execution budget and evidence rules remain in control.

## Supported direct API adapters

| Provider | API contract | Credential | Model selection |
|---|---|---|---|
| Anthropic | Messages API | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` or `AUREN_MODEL_ANTHROPIC` |
| OpenAI | Responses API | `OPENAI_API_KEY` | `OPENAI_MODEL` or `AUREN_MODEL_OPENAI` |
| Google Gemini | OpenAI-compatible Chat Completions | `GEMINI_API_KEY` | `GEMINI_MODEL` or `AUREN_MODEL_GEMINI` |
| Groq | OpenAI-compatible Chat Completions | `GROQ_API_KEY` | `GROQ_MODEL` or `AUREN_MODEL_GROQ` |
| xAI / Grok | Responses API | `XAI_API_KEY` | `XAI_MODEL` or `AUREN_MODEL_XAI` |
| OpenRouter | OpenAI-compatible Chat Completions | `OPENROUTER_API_KEY` | `OPENROUTER_MODEL` or `AUREN_MODEL_OPENROUTER` |
| Together AI | OpenAI-compatible Chat Completions | `TOGETHER_API_KEY` | `TOGETHER_MODEL` or `AUREN_MODEL_TOGETHER` |
| Fireworks | OpenAI-compatible Chat Completions | `FIREWORKS_API_KEY` | `FIREWORKS_MODEL` or `AUREN_MODEL_FIREWORKS` |
| DeepSeek | OpenAI-compatible Chat Completions | `DEEPSEEK_API_KEY` | `DEEPSEEK_MODEL` or `AUREN_MODEL_DEEPSEEK` |
| Mistral | OpenAI-compatible Chat Completions | `MISTRAL_API_KEY` | `MISTRAL_MODEL` or `AUREN_MODEL_MISTRAL` |
| Cerebras | OpenAI-compatible Chat Completions | `CEREBRAS_API_KEY` | `CEREBRAS_MODEL` or `AUREN_MODEL_CEREBRAS` |
| Perplexity | OpenAI-compatible Chat Completions | `PERPLEXITY_API_KEY` | `PERPLEXITY_MODEL` or `AUREN_MODEL_PERPLEXITY` |
| Cohere | Cohere Chat API | `COHERE_API_KEY` | `COHERE_MODEL` or `AUREN_MODEL_COHERE` |
| Custom OpenAI-compatible API | Chat Completions | `AUREN_CUSTOM_PROVIDER_API_KEY` | `AUREN_CUSTOM_PROVIDER_MODEL` |

Model IDs are explicit configuration. AUREN does not hard-code a model catalogue that can silently become stale.

## Configure one or more providers

For a copy-and-run guide covering Linux/macOS, Windows PowerShell, local Ollama, MCP clients and orchestration frameworks, see [Multi-LLM and AI Infrastructure Quickstart](MULTI_PROVIDER_QUICKSTART.md).

Set the ordered provider chain in the environment of the AUREN runtime:

```bash
export AUREN_REMOTE_PROVIDER_ORDER=groq,openai,xai
export GROQ_API_KEY="<secret-manager-injected-value>"
export GROQ_MODEL="<current-model-id>"
export OPENAI_API_KEY="<secret-manager-injected-value>"
export OPENAI_MODEL="<current-model-id>"
export XAI_API_KEY="<secret-manager-injected-value>"
export XAI_MODEL="<current-model-id>"
```

Use the secret manager for credentials in deployed environments. Do not commit a `.env` file containing secrets. Do not place API keys in task descriptions, model context, approval metadata, evidence, or logs.

### Custom OpenAI-compatible endpoint

For a provider not in the built-in catalog, configure a Chat Completions endpoint and explicitly allowlist its hostname:

```bash
export AUREN_REMOTE_PROVIDER_ORDER=custom,groq,openai
export AUREN_CUSTOM_PROVIDER_URL=https://models.example.net/v1/chat/completions
export AUREN_CUSTOM_PROVIDER_ALLOWED_HOSTS=models.example.net
export AUREN_CUSTOM_PROVIDER_API_KEY="<secret-manager-injected-value>"
export AUREN_CUSTOM_PROVIDER_MODEL="<current-model-id>"
```

Custom endpoints must use HTTPS, port 443, a `/chat/completions` path, no URL credentials/query/fragment, and an exact hostname present in `AUREN_CUSTOM_PROVIDER_ALLOWED_HOSTS`. DNS answers are still checked and pinned by guarded transport. This path is for OpenAI-compatible Chat Completions APIs only; native provider protocols need a reviewed adapter.


The router creates a remote provider pool only when `AUREN_REMOTE_PROVIDER_ORDER` is explicitly configured. Providers without both a key and model ID are skipped. Unknown provider names and duplicate configured providers fail closed.

`AUREN_REMOTE_PROVIDER_ORDER` defines the deployment's configured provider pool. The routing API now supports a per-request ordered subset through `provider_order`; persisted per-user defaults and tenant-scoped provider credentials are not yet implemented. When a user connects ChatGPT, Claude, Codex, Gemini or another MCP host, that host's own model selector remains the user's model choice; AUREN does not import the host's private API credentials.

The pool attempts providers in the exact configured order. One provider is attempted per supervisor-charged remote attempt. The existing shared execution guard remains authoritative: the provider pool cannot increase the task's attempt count or deadline. The route currently has a maximum of three attempts, and any local attempt consumes that same budget.

## Per-request provider selection

The local routing API exposes the configured provider catalog without exposing credentials:

- `GET /api/providers` returns provider IDs and configured model IDs.
- `POST /api/route` accepts an optional ordered `provider_order` array, for example `["groq", "openai"]`.
- The request may select only providers that the host has configured. Unknown, duplicate or unavailable providers are rejected; AUREN does not silently substitute another provider.
- The selected order is honored within the shared supervisor attempt and deadline budget. A shorter user-selected chain also has a shorter retry budget.
- Private and restricted tasks remain local-only even when a remote provider order is supplied.

The host administrator still controls which provider credentials and model IDs are available. End users can choose an ordered subset per routing request, but they cannot add credentials, arbitrary endpoints or unreviewed providers through the request.
## Privacy and authority

- Remote inference is eligible only when the task privacy is explicitly `public` or `non_sensitive`.
- Private and restricted tasks remain local; they do not fall back to remote API providers.
- API endpoints are fixed in the provider registry. Each request uses guarded HTTPS with the exact provider hostname allowlisted, DNS answers checked and pinned, redirects disabled, and bounded response bytes.
- Credentials are read from the named environment variable and used only in the request header. They are not included in response metadata.
- AUREN does not execute provider-returned tool calls. Any normalized tool-call data is returned as metadata for a separately authorized AUREN tool workflow.
- The model is not an approver, repository authority, release authority, or evidence verifier.
- Provider failures move to the next configured provider only within the remaining shared attempt and time budget. There is no unbounded retry loop.

## Runtime limits

| Limit | Current value |
|---|---:|
| Prompt size | 256 KiB |
| Provider response | 2 MiB |
| Output token request | 4,096 |
| Provider timeout | 0.1–120 seconds |
| Remote provider attempts | At most 3, also bounded by the task's remaining shared budget |
| Streaming | Not implemented in this adapter |
| Tool execution | Not implemented; normalized tool calls are never executed |
| Automatic model discovery | Not implemented; model IDs are explicit |
| Cost calculation | Usage metadata is captured; provider price tables and cost totals are not yet implemented |

## What still needs live validation

Repository tests use mocked transport and do not contact vendor APIs. Before enabling a provider in production:

1. Inject a deployment-owned credential through the secret manager.
2. Set a current model ID supported by that account and region.
3. Run a read-only conformance probe against the provider.
4. Verify authentication, rate limits, timeout behavior, malformed responses, failover and cancellation.
5. Record provider/model version, usage, latency, cost, request digest and outcome.
6. Run paired quality and safety evaluations before allowing automatic task routing.
7. Confirm the provider's retention, training, residency and data-processing terms for the task's privacy classification.

## MCP hosts are a separate layer

ChatGPT, Codex, Claude, Gemini, Cursor, VS Code and other MCP clients connect to AUREN's operator service. The direct model API adapters above are used by AUREN's own HybridModelRouter. Connecting a host to AUREN over MCP does not automatically grant AUREN access to that host's model API or credentials. Conversely, configuring an inference provider does not create an IDE plugin or desktop connector.

See [Platform Compatibility Matrix](PLATFORM_COMPATIBILITY_MATRIX.md) and [Remote MCP Operations](REMOTE_MCP_OPERATIONS.md) for the host-side integration boundary.
