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

1. AUREN has no published ChatGPT plugin or remote MCP service. ChatGPT web cannot connect directly to AUREN's local stdio server.
2. The current MCP capability exposes a durable task trigger, not the complete AUREN operator surface. It is not a remote HTTP MCP server.
3. The documented Gemini MCP command pointed to `portable/agency_mcp.py`, which is a protocol helper, not the stdio server. The correct entry point is `portable/chat_capability.py`.
4. Cursor can use the existing `AGENTS.md` and `.agents/skills`, but AUREN lacked a Cursor-specific rule and setup guidance.
5. Groq and xAI/Grok are model/API integrations, not drop-in coding-agent CLIs. AUREN currently has no reviewed API adapters, key allowlists, model catalog entries, or live conformance tests for them.
6. The compatibility harness is static by default. Live checks for installed vendor clients and desktop/IDE workflows remain opt-in or manual.

## Compatibility matrix

| Product surface | Status | Available AUREN path | Main gap |
|---|---|---|---|
| ChatGPT web | Partial | Custom MCP app / plugin-distributed skill | No remote MCP endpoint or ChatGPT plugin |
| ChatGPT desktop / Codex in ChatGPT | Partial | `AGENTS.md`, Agent Skills, MCP | No published AUREN plugin or desktop acceptance run |
| Codex CLI | Repository configured | `AGENTS.md`, `.agents/skills`, `codex exec`, MCP | Live CLI and MCP acceptance not recorded |
| Codex app | Repository configured | Same project guidance, skills and MCP concepts | App-specific smoke test not in CI |
| Claude chat web | Chat only | Project files / supplied context; remote connectors if configured | No local repo execution from ordinary chat |
| Claude Code CLI | Repository configured | `CLAUDE.md`, skill, plugin, hooks, MCP, guarded CLI bridge | Live provider and permission-mode checks |
| Claude Desktop | Repository configured | Claude Code Code tab, plugin and MCP | Desktop-specific install/test not recorded |
| Claude Code for VS Code | Repository configured | `CLAUDE.md`, skill, plugin and MCP | VS Code extension smoke test not in CI |
| Microsoft Visual Studio IDE | Not native | External AUREN CLI or MCP host | No dedicated Visual Studio extension |
| Gemini CLI | Repository configured | `GEMINI.md`, `AGENTS.md`, skill, MCP, headless mode | Corrected MCP command needs host smoke test |
| Gemini Code Assist in VS Code | Partial | Repository instructions; Gemini CLI companion as a separate path | No dedicated Code Assist package or test |
| Cursor | Partial | `AGENTS.md`, `.agents/skills`, MCP | Cursor rule and setup were missing; Cloud Agent behavior needs separate validation |
| Groq API | Not wired | OpenAI-compatible API and remote MCP are available upstream | No AUREN API provider adapter |
| Grok chat web | Chat only | Supplied context only | No AUREN repository execution surface |
| xAI API / Grok API | Not wired | OpenAI-compatible Responses API and remote MCP are available upstream | No AUREN API provider adapter |
| Grok Build CLI | Partial | Remote MCP is available upstream | No AUREN-specific local CLI/skill package |
| GitHub Copilot | Repository configured | `AGENTS.md`, `.github/copilot-instructions.md`, skills, MCP | Copilot Agent-mode acceptance not in CI |

The machine-readable declarations live in `.ai-harness/PROVIDER_MATRIX.json`. The values there distinguish product integration from model-provider support.

## Important product distinctions

### ChatGPT web is not Codex CLI

ChatGPT web can use custom MCP apps when the workspace, plan and admin permissions allow it. It connects to a remote MCP endpoint; it cannot directly launch AUREN's local stdio process. Full MCP write/modify support is currently workspace-plan dependent. Pro access is more limited and should not be treated as equivalent to Business or Enterprise/Edu write access.

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

OpenAI API compatibility is partial compatibility, not proof that all request fields, streaming events, tool semantics or approval features match.

## Current MCP boundary

The local stdio server is `portable/chat_capability.py`. It exposes one tool, `adaptive_runtime_trigger`, which durably accepts a task and returns a trigger receipt. It does not expose a complete remote operator API or a full suite of repository read/write tools.

`portable/agency_mcp.py` contains an MCP tool-endpoint abstraction; it is not the executable stdio server. Do not use it as the `command` in a host MCP configuration.

Before broad deployment, AUREN needs:
- A separately deployed Streamable HTTP transport for hosted clients.
- OAuth or equivalent user-bound authentication and tenant isolation.
- A tool catalog with explicit read-only versus consequential-write classification.
- Per-tool authorization and approval requirements enforced by AUREN, not delegated to the model.
- Durable request IDs, idempotency keys, status lookup and authoritative outcome reconciliation.
- Protocol conformance tests for initialize, capability negotiation, tools/list, tools/call, notifications, cancellation, errors, size limits and transport disconnects.
- Independent security review and deployment evidence.

## Prioritized implementation plan

| Priority | Work item | Acceptance evidence |
|---|---|---|
| P0 | Correct all MCP setup instructions to use `portable/chat_capability.py` | Docs test plus local stdio initialize/list/call smoke tests |
| P0 | Build remote MCP transport for ChatGPT web and hosted clients | Authenticated Streamable HTTP conformance, tenant isolation, audit and approval tests |
| P0 | Expand MCP tool surface with status and evidence retrieval | Stable schemas, read-only policy tests, durable status lookup |
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
```

The static suite checks repository declarations only. Live provider tests must be explicitly enabled and run in an environment with the relevant CLI, authentication and permission controls.

## Official product references

- [Codex customization: AGENTS.md, skills, MCP and subagents](https://learn.chatgpt.com/docs/customization/overview)
- [ChatGPT developer mode and MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt)
- [ChatGPT custom MCP server connections](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)
- [ChatGPT and Codex plugins](https://help.openai.com/en/articles/20001256-plugins-in-chatgpt)
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
