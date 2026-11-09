# AUREN orchestration and MCP integrations

AUREN separates **the model**, **the agent host/orchestrator**, and **the execution control plane**. The model can change without changing AUREN's execution contract. An MCP-capable host connects to the AUREN operator endpoint; AUREN remains responsible for workspace binding, task admission, authorization, approvals, durable status, cancellation and evidence.

## Connection contract

Use the deployed HTTPS endpoint:

`https://<your-auren-host>/mcp`

Required deployment properties:

- A trusted runtime factory with a non-empty reviewed agent graph.
- TLS termination, external OAuth 2.1 authorization server, PKCE S256 and issuer/audience validation.
- Per-user identity and least-privilege scopes. Do not share one service token across unrelated users.
- AUREN's operator approval and policy gates remain authoritative. Host-side approval is an additional control, not a replacement.
- AUREN binds the service to one configured workspace. A remote MCP client cannot choose an arbitrary server-side project path.
- Use the same idempotency key when retrying a task submission after an uncertain network outcome.
- SQLite is a single-host deployment mode. Multi-host deployments require the PostgreSQL admission and host/failover acceptance gates; do not scale this service horizontally over SQLite.

The server does not expose a general shell, arbitrary file access, or model-controlled network access.

## Supported operator tools

| Tool | Purpose | Typical scope |
|---|---|---|
| `auren.service_status` | Health, queue capacity and admission state | read |
| `auren.submit_task` | Durably accept an engineering task; returns an acceptance receipt | write |
| `auren.list_tasks` / `auren.get_task` | Task status and bounded summaries | read |
| `auren.cancel_task` | Request cancellation; active work remains nonterminal until confirmed | cancel |
| `auren.get_evidence` | Progress and evidence references | evidence |
| `auren.list_approvals` / `auren.get_approval` | Read approval records | approval-read |
| `auren.decide_approval` | Record an authorized approval decision | approval-decide |
| `auren.resume_provider_task` | Start guarded provider reconciliation | resume |
| `auren.pause_admissions` / `auren.resume_admissions` | Operator admission controls | admin |

Tool visibility is filtered by the authenticated principal's scopes. The service never trusts role or project claims supplied in tool arguments.

## OpenAI Agents SDK

The SDK supports Streamable HTTP MCP and per-tool approval policies. Install its MCP support:

```bash
pip install openai-agents
```

```python
import asyncio
import os

from agents import Agent, Runner
from agents.mcp import MCPServerStreamableHttp

async def main():
    token = os.environ["AUREN_MCP_ACCESS_TOKEN"]
    async with MCPServerStreamableHttp(
        name="AUREN",
        params={
            "url": os.environ["AUREN_MCP_URL"],
            "headers": {"Authorization": f"Bearer {token}"},
            "timeout": 30,
        },
        cache_tools_list=True,
        require_approval="always",
        max_retry_attempts=0,
    ) as server:
        agent = Agent(
            name="AUREN operator",
            instructions=(
                "Use AUREN for governed engineering work. A submit response is "
                "an acceptance receipt, not proof of completion. Poll task status "
                "and retrieve verification evidence before reporting completion."
            ),
            mcp_servers=[server],
            mcp_config={"include_server_in_tool_names": True},
        )
        result = await Runner.run(agent, "Check AUREN service status.")
        print(result.final_output)

asyncio.run(main())
```

Keep retries for write operations disabled at the host layer unless the caller reuses the same idempotency key. AUREN enforces its own durable idempotency and authorization rules.

Official reference: https://openai.github.io/openai-agents-python/mcp/

## Microsoft AutoGen

AutoGen exposes remote Streamable HTTP servers through `StreamableHttpServerParams` and `mcp_server_tools`. Install:

```bash
pip install -U "autogen-agentchat" "autogen-ext[openai,mcp]"
```

```python
import asyncio
import os

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.tools.mcp import StreamableHttpServerParams, mcp_server_tools

async def main():
    params = StreamableHttpServerParams(
        url=os.environ["AUREN_MCP_URL"],
        headers={"Authorization": f"Bearer {os.environ['AUREN_MCP_ACCESS_TOKEN']}"},
        timeout=30.0,
    )
    tools = await mcp_server_tools(params)
    agent = AssistantAgent(
        name="auren_operator",
        model_client=OpenAIChatCompletionClient(model=os.environ["MODEL_ID"]),
        tools=tools,
        system_message=(
            "AUREN owns execution and approvals. Treat task submission as acceptance only. "
            "Check task status and evidence before claiming completion."
        ),
    )
    result = await agent.run(task="Read AUREN service status.")
    print(result)

asyncio.run(main())
```

Official reference: https://microsoft.github.io/autogen/stable/reference/python/autogen_ext.tools.mcp.html

## Pydantic AI

Pydantic AI supports Streamable HTTP MCP through `MCPToolset`, including per-run credentials. Install:

```bash
pip install "pydantic-ai-slim[mcp]"
```

```python
import asyncio
import os
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPToolset

@dataclass
class UserDependencies:
    auren_token: str

def auren_auth(ctx: RunContext[UserDependencies]) -> str:
    return ctx.deps.auren_token

toolset = MCPToolset(os.environ["AUREN_MCP_URL"], auth=auren_auth)
agent = Agent(
    os.environ["MODEL_ID"],
    deps_type=UserDependencies,
    toolsets=[toolset],
    instructions="Use AUREN's task status and evidence tools to verify work.",
)

async def main():
    result = await agent.run(
        "Read AUREN service status.",
        deps=UserDependencies(os.environ["AUREN_MCP_ACCESS_TOKEN"]),
    )
    print(result.output)

asyncio.run(main())
```

Create a per-user toolset/session for multi-user workloads. Do not share one authenticated MCP session across users.

Official reference: https://pydantic.dev/docs/ai/mcp/client/

## LangChain and LangGraph

Use the LangChain-maintained MCP adapter package to discover AUREN tools and bind them to a LangChain agent or LangGraph workflow:

```bash
pip install langchain-mcp-adapters
```

Configure the AUREN server as a Streamable HTTP MCP server, provide the user's OAuth bearer token through the adapter's supported headers/auth mechanism, and keep the tool list scoped to the required AUREN capabilities. The adapter is a host-side integration; it does not bypass AUREN's server authorization, approvals or task lifecycle.

Adapter source and current examples: https://github.com/langchain-ai/langchain-mcp-adapters

## n8n workflows

Use n8n's **MCP Client Tool** node from an AI Agent workflow and configure the AUREN Streamable HTTP endpoint and OAuth credential. Start with `auren.service_status`, then expose `auren.submit_task` only to workflows whose identity has the write scope. Store credentials in n8n credentials, not workflow JSON or node text.

Do not make a workflow report completion from the submit receipt. Poll `auren.get_task`, then retrieve `auren.get_evidence` and verify the required acceptance state.

Official docs: https://docs.n8n.io/

## IDE and chat hosts

The same remote endpoint and OAuth registration can be reused by MCP-capable hosts:

- ChatGPT web custom MCP app (workspace/plan and admin policy dependent).
- Codex app and Codex CLI.
- Claude web custom connector, Claude Desktop remote connector, Claude Code CLI and Claude Code for VS Code.
- Gemini CLI and Antigravity where the installed product/version exposes MCP.
- Cursor, VS Code Copilot, Visual Studio Copilot Agent mode, Cline, Roo Code, Continue and Zed.
- OpenHands local deployments that enable MCP.

Use the host-specific configuration templates in `integrations/mcp/` and the setup matrix in [MCP Host Compatibility](MCP_HOST_COMPATIBILITY.md). ChatGPT desktop and ordinary Grok chat are not treated as local repository execution hosts. xAI/Grok API is an inference provider, not a chat/IDE execution host.

## Provider choice and multi-provider routing

AUREN's direct inference adapters currently cover OpenAI Responses, Anthropic Messages, Gemini, Groq, xAI, OpenRouter, Together, Fireworks, DeepSeek, Mistral, Cerebras and Perplexity. A custom OpenAI-compatible endpoint can cover gateways and compatible services such as LiteLLM, vLLM, LM Studio or NVIDIA NIM when their endpoint and auth behavior match the adapter contract and the hostname is explicitly allowlisted.

Configure provider priority with `AUREN_REMOTE_PROVIDER_ORDER` and each provider's API key/model through secret injection. Each remote attempt is charged to the supervisor budget. Private and restricted tasks remain local-only. The current adapter slice does not implement streaming, model-catalog discovery, automatic price calculation, arbitrary vendor tool execution, or every vendor-specific auth protocol.

**Important runtime boundary:** these direct API adapters are used by AUREN's `HybridModelRouter` (including the dashboard model-routing surface). The main graph-team crew adapters still use host-registered provider invokers and per-crew read-only tool scopes from the trusted bootstrap. The new MCP host connectors do not automatically transfer the host's model credentials into AUREN. A production deployment must configure those crew invokers explicitly; there is no silent provider substitution.

The following need dedicated adapter/conformance work before they can be called first-class direct API providers: Azure OpenAI deployment-specific auth/versioning, AWS Bedrock SigV4, Google Vertex AI OAuth/service accounts, Cohere's native API, and any provider whose OpenAI-compatible endpoint diverges in tool/schema semantics.

## Not yet first-party verified

These are not claimed as production-supported AUREN connectors until a version-pinned configuration and live acceptance record exist:

| Product/framework | Current gap |
|---|---|
| CrewAI | No AUREN-specific Streamable HTTP sample or live client acceptance |
| Dify | No AUREN-specific MCP client configuration and auth acceptance |
| Flowise | No AUREN-specific MCP client configuration and auth acceptance |
| Microsoft Semantic Kernel | No version-pinned MCP client integration acceptance |
| Haystack | No version-pinned MCP client integration acceptance |
| OpenHands remote deployment | Local MCP path is documented; remote HTTP/auth acceptance is still open |
| ChatGPT desktop | No separate desktop-specific acceptance record; use ChatGPT web custom MCP apps where enabled |
| Grok web chat | Not treated as an MCP execution host; xAI API inference is supported separately |

A host can be described as **MCP-compatible** without being **AUREN-verified**. Until its own live test passes, use a supported MCP client bridge and keep the host marked unverified.

## Acceptance gates before calling an integration production-ready

1. Register the external OAuth client and verify PKCE S256, audience/resource, issuer, expiry and per-user scope mapping.
2. Connect each installed host and record initialize, tools/list, read-only call, unauthorized call and token-expiry outcomes.
3. Verify write-scope denial, duplicate idempotency handling, approval identity, cancellation semantics and evidence retrieval.
4. Test reconnects, timeouts, 429/5xx, expired credentials, oversized responses and concurrent users.
5. Record host name/version, AUREN server build SHA, provider/model ID, test outcome and evidence digest.
6. Keep a host marked **configured** until its own live acceptance record passes. A shared MCP endpoint does not prove every client implementation is compatible.

AUREN's compatibility matrix distinguishes repository configuration from live verification. Do not promote a client or provider to production-verified based on documentation or a passing local unit test alone.
