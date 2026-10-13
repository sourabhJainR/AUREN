# Provider Contract

A provider is an execution adapter, not the orchestration brain. AUREN owns task intent, routing, context selection, budgets, safety, verification, learning and promotion. Providers supply model inference and native tool/agent capabilities.

## Supported provider surfaces

AUREN must distinguish the provider from the product surface used to invoke it:

| Provider | Primary surfaces | Repository instruction surface | AUREN integration |
|---|---|---|---|
| Claude | Claude Code CLI / IDE / Desktop / Web | `CLAUDE.md` + Agent Skills + hooks | Native skill/plugin + CLI adapter |
| Codex | Codex CLI / IDE / Desktop / Web | `AGENTS.md` + supported skills | CLI adapter + Codex/ChatGPT plugin surface |
| Gemini | Gemini CLI / Gemini Code Assist / Antigravity migration path | `GEMINI.md` (or configured context filename) + extensions | CLI adapter + optional MCP/A2A |
| ChatGPT | ChatGPT / Codex in ChatGPT / API | Project/app instructions or `AGENTS.md` when using Codex | MCP/app or Codex surface; never pretend ChatGPT is a local CLI |

Provider names are stable logical identifiers. Model names, executable names and product availability are configuration data and must not leak into task prompts.

## Required adapter semantics

The adapter should expose, when supported:

- provider identifier
- product/surface identifier
- model identifier
- reasoning/effort tier
- supported tools
- structured output capability
- streaming capability
- cancellation
- timeout behavior
- token/input/output usage
- exit status
- tool-call or step observations
- prompt/context cache capability and reported hit/miss data
- session/resume capability
- permission/sandbox mode
- native instruction/skill loading status

Unsupported fields must be reported as `unsupported` or `unavailable`; never inferred as successful.

## Agent-loop contract

The provider-facing loop must preserve this semantic cycle even when APIs differ:

```text
plan -> tool/action -> observation -> verify -> continue/stop
```

A tool result is an observation, not a completion signal. A model statement such as `done`, `verified`, or `no regression` is not proof without repository evidence.

When a provider exposes intermediate tool calls, the adapter preserves order, tool name, duration, status and compact result digest. When it does not, AUREN records a phase-level observation instead of inventing tool telemetry.

## Instruction-surface contract

AUREN maintains one canonical engineering contract but projects it into each provider's native instruction mechanism:

- Claude: keep `CLAUDE.md` lean and stable; use Agent Skills for reusable workflows and hooks for deterministic lifecycle actions. Do not duplicate the full AUREN policy into every prompt.
- Codex: `AGENTS.md` is the repository-wide entry point. Preserve hierarchical/nearest-scope behavior and keep injected context bounded.
- Gemini: `GEMINI.md` is the native context file. Prefer a small root contract and let Gemini's hierarchical/JIT context loading discover narrower instructions as files are touched.
- ChatGPT: use the connected repository/app/project or Codex surface as the execution substrate. AUREN policy must be exposed through the supported instruction/app/skill mechanism; ordinary ChatGPT chat is not assumed to execute local commands.

The same intent digest, boundaries, acceptance criteria and safety rules must survive projection. Provider-specific syntax must not change semantics.

## Context cache contract

The harness may provide content-addressed context pages. Provider adapters may map stable page digests to native prompt/KV cache facilities when supported.

The adapter must not claim a cache hit unless the provider reports one. `provider_kv_cache = adapter-dependent` is the safe default.

Stable context should be reused where the provider supports prefix/prompt caching. Dynamic task evidence, current diffs, failures and verification output should remain separate from stable context so cache invalidation is narrow.

## Tool / MCP contract

MCP is a capability transport, not a provider-specific orchestration layer. AUREN may expose narrowly scoped MCP tools to Claude, Codex/ChatGPT, Gemini or another compatible client. Tool discovery is progressive: advertise metadata first, activate only tools justified by the current phase, and enforce read/write/permission boundaries outside model instructions.

For each tool invocation retain:
`tool_id | provider | phase | input_digest | permission_mode | started | duration | status | output_digest`.

Never grant a provider broader permissions merely because another provider supports a capability.

## Normalized result

The orchestrator normalizes provider output into:

```text
status
provider
surface
model
phase
duration
usage
artifacts
text
structured_data
tool_observations
cache
permissions
error
```

`cache` distinguishes AUREN page/cache reuse from provider-side prompt/KV-cache hits. `permissions` distinguishes requested, granted and actually used capabilities.

## Capability negotiation

Before execution, negotiate the minimum required capability set. Do not request a feature the provider does not support. Fall back to the nearest safe execution mode.

Capability decisions are evidence-backed and recorded in `capability-plan.json`.

## Failure semantics

Timeout, cancellation, unavailable executable, authentication failure, permission denial, invalid structured output, tool failure and non-zero provider exit are distinct conditions.

A provider failure must not automatically become an application-code failure. Conversely, a successful provider exit does not imply that repository work is correct.

## Cross-provider parity

AUREN's conformance suite should run the same representative task contracts against every available provider surface. Parity means equivalent safety, scope, evidence and acceptance behavior, not identical text or tool sequences.

At minimum test:

1. trivial read-only task;
2. focused code change;
3. failing-test/debug task;
4. high-risk task requiring review/grill;
5. context-overflow/progressive-discovery task;
6. provider/tool unavailable fallback;
7. malformed/unsupported output;
8. cancellation/timeout;
9. resume after checkpoint;
10. self-improvement candidate rejected by regression/safety gate.

The provider adapter is passing only when the harness can prove the expected contract and distinguish unsupported capabilities from successful ones.
