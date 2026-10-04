# AUREN Usage and Platform Integration

AUREN is a provider-neutral engineering runtime. The canonical runtime, state and package names are AUREN; no host integration is a hard dependency.

## 1. Install AUREN

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
rm -rf ~/.auren
~~~

For a system-scoped service use `--scope system` with the required privileges.

Older installations may have left `~/.aer`; remove that directory only after any legacy service has been stopped/uninstalled:

~~~bash
rm -rf ~/.aer
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

Legacy public/runtime names such as `aer_cli.py`, `aer-portable.zip`, `aer-bundle.json`, `.aer/`, and `aer-core/` are not part of the current distribution contract.

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

## 5. Claude Desktop / MCP

If the host supports local MCP servers, register AUREN's stdio MCP server:

~~~json
{
  "mcpServers": {
    "auren": {
      "command": "python",
      "args": ["/absolute/path/to/.auren/current/portable/agency_mcp.py"]
    }
  }
}
~~~

Use the MCP configuration mechanism provided by the installed Claude Desktop version. Do not copy this JSON into an unrelated host configuration file.

MCP is an integration surface, not the source of AUREN's planning, verification or learning authority.

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
gemini mcp add auren python /absolute/path/to/.auren/current/portable/agency_mcp.py --scope user
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
