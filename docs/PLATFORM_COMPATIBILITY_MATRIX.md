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

1. AUREN now has an authenticated remote Streamable HTTP MCP service and a broader operator tool surface. It is implemented in the repository but not yet deployed or live-tested with vendor clients.
2. The service uses an external OAuth authorization server for hosted-client login. AUREN validates issuer, audience, expiry and scopes through token introspection; it does not implement the authorization server, PKCE, DCR or CIMD itself.
3. Codex, Claude, Gemini, Cursor and VS Code remote MCP setup paths are documented. Client-specific OAuth and desktop/IDE acceptance remains open.
4. Claude web and Claude Desktop support remote MCP connectors; this does not give those chat surfaces local filesystem access. AUREN executes tasks on its configured host.
5. Cursor now has a project rule and remote MCP setup guide. Antigravity CLI/IDE and Visual Studio 2022 17.14+/2026 are also documented as remote MCP hosts; none has passed live AUREN acceptance yet.
6. Groq and xAI/Grok remain separate model-provider integrations. The remote MCP service does not add their inference adapters.
7. The compatibility harness is static by default. Live checks for installed vendor clients and desktop/IDE workflows remain opt-in or manual.

## Compatibility matrix

| Product surface | Status | Available AUREN path | Main gap |
|---|---|---|---|
| ChatGPT web | Partial | Remote Streamable HTTP MCP, OAuth tool metadata, profile identity | External OAuth 2.1 authorization server, ChatGPT app setup and live acceptance |
| ChatGPT desktop / Codex in ChatGPT | Partial | `AGENTS.md`, Agent Skills, remote MCP | OAuth connection and desktop acceptance not recorded |
| Codex CLI | Repository configured | `AGENTS.md`, skills, remote MCP, `codex exec` | Live OAuth and tool acceptance not recorded |
| Codex app | Repository configured | Shared Codex MCP configuration and skills | App-specific live acceptance not recorded |
| Claude chat web | Partial | Remote MCP custom connectors, OAuth | AUREN endpoint deployment and Claude connector acceptance |
| Claude Code CLI | Repository configured | `CLAUDE.md`, skill, plugin, hooks, remote MCP | Live provider and permission-mode checks |
| Claude Desktop | Partial | Remote MCP Connectors and Claude Code Code tab | Connector/OAuth acceptance not recorded; remote tools execute on AUREN host |
| Claude Code for VS Code | Repository configured | `CLAUDE.md`, skill, plugin and remote MCP | VS Code extension smoke test not in CI |
| Microsoft Visual Studio IDE | Not native | External AUREN CLI or MCP host | No dedicated Visual Studio extension |
| Gemini CLI | Repository configured | `GEMINI.md`, `AGENTS.md`, skill, remote HTTP MCP, headless mode | OAuth and host smoke test not recorded; some consumer plans have moved to Antigravity |
| Gemini Code Assist in VS Code | Partial | Agent mode can use MCP through Gemini CLI settings | AUREN connection not tested; product/plan-specific migration applies |
| Cursor | Partial | `AGENTS.md`, skills, `.cursor/rules/auren.mdc`, remote MCP | OAuth and local/cloud/SSH acceptance still open |
| Groq API | Not wired | OpenAI-compatible API and remote MCP are available upstream | No AUREN API provider adapter |
| Grok chat web | Chat only | Supplied context only | No AUREN repository execution surface |
| xAI API / Grok API | Not wired | OpenAI-compatible Responses API and remote MCP are available upstream | No AUREN API provider adapter |
| Grok Build CLI | Partial | Remote MCP is available upstream | No AUREN-specific local CLI/skill package |
| GitHub Copilot | Repository configured | `AGENTS.md`, `.github/copilot-instructions.md`, skills, MCP | Copilot Agent-mode acceptance not in CI |

The machine-readable declarations live in `.ai-harness/PROVIDER_MATRIX.json`. The values there distinguish product integration from model-provider support.

## Important product distinctions

### ChatGPT web is not Codex CLI

ChatGPT web can use custom MCP apps when the workspace, plan and admin permissions allow it. It connects to a remote MCP endpoint; it cannot directly launch AUREN's local stdio process. Full MCP write/modify support is currently limited to Business and Enterprise/Edu workspaces. Pro access is limited to read/fetch actions; Free does not provide this custom MCP write path. Do not treat plan access as equivalent across tiers.

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
- Use `python /absolute/path/to/.auren/current/portable/chat_capability.py` as the local stdio MCP command.
- Verify `/skills list`, `/mcp list`, and a headless JSONL request.
- Add a Gemini-specific parser for structured output and failure/turn-limit exit codes.
- Test Gemini Code Assist separately before listing it as fully supported.

### Cursor

Cursor supports `AGENTS.md`, project rules, Agent Skills and MCP. AUREN's shared `.agents/skills` directory is the right portable skill location. Cursor's local user skills are not automatically copied to Cloud Agents or remote workers; keep the AUREN skill in the repository for those environments.

**Required AUREN work:**
- Use the new `.cursor/rules/auren.mdc` rule as a short pointer to `AGENTS.md` and the AUREN skill.
- Add the local MCP server through Cursor's MCP settings or project config using `portable/chat_capability.py`.
- Validate local Agent, Cloud Agent, remote SSH and self-hosted worker paths independently.

### Groq and xAI/Grok

Groq and xAI expose model APIs and MCP-related capabilities. They are inference backends, not substitutes for Claude Code, Codex or Cursor as repository execution hosts.

AUREN's current guarded CLI bridge only recognizes Claude, Codex and Gemini executables. The API provider layer does not yet register Groq or xAI, and the environment allowlist does not permit `GROQ_API_KEY` or `XAI_API_KEY`. Do not add these keys to a broad environment pass-through or simply add provider names to the CLI allowlist.

**Required AUREN work for each API provider:**
1. Implement a dedicated provider adapter with a fixed base URL, bounded request/response sizes, timeouts, retry budget and cancellation.
2. Add a secret-broker path for the provider key; never persist the key in task payloads, logs or evidence.
3. Normalize tool calls, streaming, usage, errors and model capabilities into AUREN's provider contract.
4. Record model ID, provider version, token usage, latency, cost and request digest.
5. Test tool-call replay, malformed responses, rate limits, timeout-after-acceptance and provider outage.
6. Run paired quality, cost, latency and safety evaluations before allowing automatic routing.

OpenAI API compatibility is partial compatibility, not proof that all request fields, streaming events, tool semantics or approval features match. In particular, xAI's remote MCP implementation does not currently support the Responses API `require_approval` and `connector_id` parameters, so AUREN must enforce its own approval boundary and tool allowlist.

## Current MCP boundary

The local stdio server remains `portable/chat_capability.py` and is intended for local MCP hosts. The remote operator service is now implemented in:

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
| P2 | Add Groq API adapter | Provider contract, key isolation, tool-call and fault-injection tests |
| P2 | Add xAI API adapter | Provider contract, remote MCP policy, tool-call and fault-injection tests |
| P2 | Add GitHub Copilot and Gemini Code Assist host acceptance | Product-specific smoke tests; no inference from shared file support |
| P2 | Add CI environment with vendor CLI installations where licensing permits | Version-pinned, non-mutating provider probes and published evidence |

## Verification commands

From an AUREN checkout:

```bash
python scripts/provider_conformance.py --json
python -m unittest tests.test_provider_conformance -v
python -m unittest tests.test_ai_coding_system_practices -v
python -m unittest discover -s tests -p '*mcp*' -v
python -m unittest tests.test_remote_mcp_service -v
```

The static suite checks repository declarations only. Live provider tests must be explicitly enabled and run in an environment with the relevant CLI, authentication and permission controls.

## Official product references

- [Codex customization: AGENTS.md, skills, MCP and subagents](https://learn.chatgpt.com/docs/customization/overview)
- [ChatGPT developer mode and MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt)
- [ChatGPT custom MCP server connections](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)
- [ChatGPT and Codex plugins](https://help.openai.com/en/articles/20001256-plugins-in-chatgpt)
- [Claude custom remote MCP connectors](https://support.anthropic.com/en/articles/11503834-building-custom-integrations-via-remote-mcp)
- [Claude and Claude Desktop remote connectors](https://support.anthropic.com/en/articles/11175166-about-custom-integrations-using-remote-mcp)
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
