# AUREN Usage and Platform Integration

AUREN is a provider-neutral engineering runtime. Start a local project with `auren up`; use the [Unified Interaction Contract](UNIFIED_INTERACTION_CONTRACT.md) to understand the same task lifecycle across MCP, CLI, dashboard and hosted clients. For copy-and-run setup across multiple model APIs, local Ollama, MCP clients and orchestration frameworks, see the [Multi-LLM and AI Infrastructure Quickstart](MULTI_PROVIDER_QUICKSTART.md). The canonical runtime, state and package names are AUREN; no host integration is a hard dependency. See the [Cross-Platform Compatibility Matrix](PLATFORM_COMPATIBILITY_MATRIX.md) for verified repository artifacts, product-surface limitations and prioritized integration gaps.

## 1. Install AUREN

Choose the latest main snapshot, latest stable release, or any older version from the [AUREN downloads page](https://sourabhJainR.github.io/AUREN/downloads.html). Each published build includes the portable bundle, Linux/macOS installer, Windows installer and SHA-256 checksum. Main snapshots are prereleases pinned to the exact merged commit; stable version tags remain immutable.

### Latest release

Linux/macOS:

~~~bash
curl -fsSL https://raw.githubusercontent.com/sourabhJainR/AUREN/main/install.sh | bash
~~~

Windows PowerShell:

~~~powershell
irm https://raw.githubusercontent.com/sourabhJainR/AUREN/main/install.ps1 | iex
~~~

### Exact release

Set AUREN_VERSION to a release tag such as v1.0.6 or a main snapshot tag such as main-a1b2c3d4e5f6 to install that exact build.

~~~bash
AUREN_VERSION=v1.0.4 curl -fsSL https://raw.githubusercontent.com/sourabhJainR/AUREN/main/install.sh | bash
~~~

The installer installs the immutable runtime under `~/.auren` and does not modify the target repository.

### Source checkout

~~~bash
git clone https://github.com/sourabhJainR/AUREN.git
cd AUREN
python auren_cli.py build --output auren-portable.zip
python auren_cli.py verify auren-portable.zip
python auren_cli.py install auren-portable.zip --skill auto
~~~

## One-command local start

After installing AUREN, open any existing or new project and run:

~~~bash
auren up
~~~

From a source checkout:

~~~bash
python auren_cli.py up
~~~

The command creates the minimal `.auren/project.json` only when it is missing, validates an existing profile without overwriting it, starts the canonical local runtime, dashboard and authenticated MCP endpoint, backs up and configures detected supported local clients, and opens the desktop console in the default browser.

Use `auren up --no-connect` to leave client settings untouched, or `auren up --no-browser` on a headless machine. Use `auren local` when you want the server without automatic client configuration. Configuration failures are reported per client and do not prevent local services from starting.

Task operations from the CLI use the same authenticated local MCP service as chat and IDE clients:

~~~bash
auren task submit --task "Implement the requested change"
auren task list
auren task status <task-id>
auren task evidence <task-id>
auren task cancel <task-id> --reason "No longer needed"
auren task approvals --status pending
auren task decide-approval <approval-id> --decision approve --reason "Reviewed"
~~~

Submission prints its caller-scoped idempotency key. Reuse `--idempotency-key` when retrying the same request. The local CLI refuses non-loopback endpoints; use an authenticated MCP-capable host for remote deployments. Start the local service first with `auren up`.

The common interaction path is:
- **Chat, IDE and desktop clients:** connect to AUREN through the local MCP server.
- **CLI:** use `auren task submit/list/status/cancel/evidence/approvals/decide-approval` for the same authenticated MCP task API, plus `auren progress --json`, `auren continuous status`, and `auren providers`.
- **Desktop console:** the browser opens the same local Session Control Center and durable progress view.
- **Hosted chat clients:** deploy the authenticated remote MCP service and configure its HTTPS endpoint and OAuth provider. A loopback URL cannot be used from a cloud-hosted chat service.

AUREN uses the same host-owned task controller and project-scoped durable progress journal across its local MCP, CLI progress view and dashboard. The MCP service remains the authority for Guard, approvals, budgets and evidence; the dashboard is an observation surface, not a second execution authority. Task acceptance is not task completion; check durable task state and evidence before treating work as done.

## 2. Upgrade, rollback and uninstall

Check for an update:

~~~bash
python ~/.auren/current/auren_cli.py check-update
~~~

Upgrade:

~~~bash
python ~/.auren/current/auren_cli.py update
~~~

Rollback to the previous pinned installation:

~~~bash
python ~/.auren/current/auren_cli.py rollback
~~~

A rollback is deliberately previous-version based. There is no arbitrary `--version` rollback flag.

Before deleting `~/.auren`, uninstall any service that was installed:

~~~bash
python ~/.auren/current/portable/maintenance_service.py --scope user uninstall
rm -rf ~/.aer
~~~

For a system-scoped service use `--scope system` with the required privileges.

Older installations may have left `~/.aer`; remove that directory only after any legacy service has been stopped/uninstalled:

~~~bash
rm -rf ~/.auren
~~~

## 3. Canonical package layout

The public package uses one naming scheme:

~~~text
AUREN/
~/.auren/
auren_cli.py
auren-portable.zip
auren-bundle.json
portable/auren_runtime.py
portable/auren_console.py
auren-core/
~~~

Historical entry-point, archive, manifest, and native-core names are catalogued in the [Legacy Name and Compatibility Register](LEGACY_NAME_COMPATIBILITY.md).

## 4. Claude Code

AUREN ships the Agent Skill under:

~~~text
skills/ai-coding-orchestrator/
~~~

For a repository-local setup, link or copy that skill into:

~~~text
.claude/skills/ai-coding-orchestrator/
~~~

The portable installer can also activate the Claude integration when Claude Code is installed:

~~~bash
python ~/.auren/current/auren_cli.py update --skill claude
~~~

Then start Claude Code in the target repository and give the task in normal language. Keep repository-specific constraints in the repository's native instruction files.

Example:

~~~text
Fix the failing payment export test.
Inspect repository instructions first.
Trace the failure with evidence.
Make the smallest compatible change.
Add regression coverage and verify the result.
Do not change unrelated behavior.
~~~

## 5. Claude Desktop and Claude web

Use the product's remote MCP connector/custom connector UI and authenticate through the configured external OAuth provider. This gives Claude remote AUREN operator tools; it does not give Claude Desktop local filesystem access. Repository execution remains on the AUREN host.

MCP is an integration surface, not the source of AUREN's planning, verification or learning authority.

## 5.1 Local stdio MCP clients

For a local MCP host that supports stdio, use `portable/chat_capability.py` with a deployment-owned runtime factory. The server fails closed if the factory is missing or returns an empty graph.

Set these environment values in the MCP host's process configuration:

- `AUREN_MCP_RUNTIME_FACTORY=trusted_module:create_runtime`
- `AUREN_PROJECT_ROOT=/absolute/path/to/workspace`

The factory must accept `project_root` and return a configured AdaptiveRuntime-compatible host with a non-empty reviewed graph, execution controller and durable trigger runtime. The tool's project path must match the configured workspace.


## 6. Codex CLI and ChatGPT Work

For Codex CLI, expose the same `skills/ai-coding-orchestrator/` Agent Skill through the supported skills directory, or use the repository's `AGENTS.md` for durable project rules.

For ChatGPT/Codex/ChatGPT Work environments, use the product's supported Agent Skills, MCP or connected-tool mechanism when available. Do not assume a managed or hosted environment can read a local filesystem configuration.

A safe pattern is:

~~~text
AUREN owns repository inspection, planning, evidence, verification and learning.
The host LLM provides model reasoning.
The host must not bypass AUREN's verification, Guard or Arena evidence gates.
~~~

## 7. GitHub Copilot

For repository-specific Copilot use, keep durable rules in:

~~~text
AGENTS.md
.github/copilot-instructions.md
~~~

AUREN's Agent Skill can be exposed through a Copilot-supported skills location such as `.github/skills/`, `.claude/skills/`, or `.agents/skills/`.

Where MCP is supported, connect AUREN's MCP server. Otherwise, invoke AUREN's CLI separately and use the resulting evidence/report in the Copilot workflow.

Do not make Copilot a hard dependency of AUREN.

## 8. Gemini CLI

Gemini CLI supports the Agent Skills format. Install the AUREN skill from the repository:

~~~bash
gemini skills install https://github.com/sourabhJainR/AUREN.git --path skills/ai-coding-orchestrator
~~~

Or link a local checkout:

~~~bash
gemini skills link ./skills/ai-coding-orchestrator
~~~

Verify discovery:

~~~text
/skills list
~~~

If you use the workspace scope, use the scope option supported by your installed Gemini CLI version.

Gemini can also connect to an AUREN MCP server where MCP is enabled:

~~~bash
gemini mcp add --transport http auren https://mcp.example.com/mcp
~~~

## 9. Grok, Kimi and other released LLM hosts

Do not assume a provider-specific feature exists.

Use the strongest supported AUREN surface in this order:

1. Agent Skills, when the host supports the standard.
2. MCP, when the host supports local or remote MCP.
3. CLI/tool calling, when the host can execute local commands.
4. HTTP/API adapter, when an authenticated AUREN service is explicitly deployed.
5. Chat-only operation, where AUREN runs separately and its evidence/report is supplied to the model.

The host model remains replaceable. AUREN's execution, verification, evidence and learning contracts remain provider-neutral.

## 10. Local LLMs

A local model can be used without changing the AUREN architecture:

~~~text
Local LLM
    |
    v
AUREN provider adapter
    |
    +--> context / retrieval
    +--> planning / routing
    +--> tool execution
    +--> verification / review
    +--> evidence / learning
~~~

The local model is an inference provider, not an authority boundary. AUREN can continue to run with no external model provider when the requested operation is deterministic or uses a configured local backend.

## 11. Optional providers and skills

Third-party skills, memory providers, MCP servers and model providers are optional. AUREN must degrade to repository-native evidence and deterministic execution when an optional provider is unavailable.

Never grant an optional provider more authority than the AUREN policy and Guard contracts allow.

## 12. Operating modes

### Implement

~~~text
Implement the requested change. Inspect repository instructions first, identify the root cause or design constraints with evidence, make the smallest safe change, add regression coverage, verify it, review it, and report open risks.
~~~

### Research

~~~text
Research the problem without modifying files. Separate repository facts, inferences, unknowns and recommendations. Cite the evidence paths used.
~~~

### POC

~~~text
Build a disposable proof of concept. Define the hypothesis, success threshold, experiment, measurement and conclusion. Do not productionize it.
~~~

### Review

~~~text
Review the current change for specification compliance, regression risk, architecture, data model, error handling, observability, security and operational behavior. Do not modify files.
~~~

## 13. Golden path

~~~text
Request
  -> repository instructions
  -> targeted evidence
  -> contract / plan
  -> implementation or research
  -> verification
  -> review
  -> evidence
  -> learning only after independent gates
~~~

For small, settled tasks, AUREN may skip unnecessary stages. It should not manufacture process for its own sake.

## 14. Troubleshooting

### Skill not discovered

Check that `SKILL.md` exists in the host's supported directory, its frontmatter is valid, and the host has refreshed its skill registry.

### Too many questions

Tell the host to proceed with bounded assumptions unless a missing answer materially changes correctness, safety, architecture, scope or acceptance.

### Optional provider unavailable

Continue with repository-native retrieval and deterministic verification. Optional integrations must never become a single point of failure.

### Need to inspect the runtime

~~~bash
python ~/.auren/current/auren_cli.py status --json
~~~

### Need to verify a downloaded bundle

~~~bash
python auren_cli.py verify auren-portable.zip
~~~

## 15. Production boundary

AUREN is responsible for bounded execution, evidence and learning contracts. The surrounding host remains responsible for its own model credentials, permissions, user approval and platform-specific configuration.

AUREN does not silently modify credentials, Git remotes, hooks, production access or merge authority.
