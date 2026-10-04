# Usage and Platform Integration

This guide explains how to use AUREN as a portable engineering operating layer with Claude Code, Codex CLI, Gemini CLI, and other Agent Skills-compatible hosts.

## 1. Mental model

You do not need to learn an internal command language.

Describe the outcome:

```text
Fix duplicate tenant export rows without changing the public API.
```

The orchestrator should infer the workflow, inspect repository conventions, grill consequential ambiguity, build a compact contract, gather targeted evidence, make the smallest safe change, verify it, and report remaining risks.

The normal spine is:

```text
Request
  -> classify
  -> grill if needed
  -> compact specification
  -> targeted repository evidence
  -> implementation / research / POC / debug / review
  -> verification
  -> independent review when warranted
  -> handoff + evidence
```

For small, settled work, stages are skipped. Do not force every task through the complete spine.

## 2. Install once

Clone the repository somewhere stable:

```bash
git clone https://github.com/sourabhJainR/AUREN.git
cd Hello_world_With_Structure
```

Validate it before installing:

```bash
python3 scripts/validate_plugin.py
python3 scripts/run_evals.py
python3 -m unittest discover -s tests -v
```

Install the local skill/plugin using the repository installer when supported:

```bash
./install.sh
```

Or:

```bash
python3 scripts/install_skill.py --auto
```

The installer backs up existing files and does not silently install third-party tools or change MCP/permissions.

## 3. Claude Code

Claude Code supports filesystem-based Agent Skills. A project skill lives under `.claude/skills/<skill-name>/SKILL.md`; a personal skill can live under `~/.claude/skills/<skill-name>/SKILL.md`. Claude can discover skills automatically and they can also be invoked explicitly. citeturn0search0turn0search10

### Recommended project setup

From the repository root, expose the orchestrator as a project skill through the package/install mechanism, or link/copy the skill directory into:

```text
.claude/skills/auren/
```

The directory should contain the existing `SKILL.md` and its referenced resources.

Then start Claude Code in the target repository:

```bash
cd ~/src/my-service
claude
```

Check that the skill is discovered using Claude Code's skills interface if available, or invoke it explicitly with its skill name. Claude Code also watches skill directories for changes during a session in supported locations. citeturn0search0

### Real Claude Code task

```text
Implement JIRA-4821: add tenant-level export filtering.

Requirements:
- preserve the existing public API
- follow the repository's existing authorization/filtering pattern
- add regression coverage
- do not refactor unrelated code

Start by grilling any ambiguity and inspect the existing export flow before changing code.
```

Expected orchestration:

```text
Jira/task
  -> identify repository rules
  -> inspect export entry point
  -> trace authorization/filtering
  -> identify data model and callers
  -> create compact contract
  -> implement minimal change
  -> run focused tests
  -> run relevant regression tests
  -> architecture/operational review
  -> final evidence
```

If the requirement is already completely settled, the system should not waste a turn asking questions.

## 4. Claude Code: research mode

Ask explicitly for research when you do not want code changes:

```text
Research why the reporting API can time out under high-cardinality tenant queries.
Do not modify files.
Trace the actual repository flow and use logs/tests/configuration if available.
Separate facts, inferences, and unknowns.
Give me a detailed evidence-backed report.
```

The result should contain evidence, flow, findings, unknowns, and confidence rather than a speculative explanation.

## 5. Claude Code: POC mode

Use POC when feasibility is unknown:

```text
Build a disposable POC to determine whether the current serializer can process
1 million records under 500 ms p95. Do not productionize the POC.
Define the experiment, success threshold, measurement method, and conclusion.
```

The orchestrator should keep the POC isolated from production architecture and explicitly mark whether any artifact is reusable.

## 6. Codex CLI

Codex supports Agent Skills with `SKILL.md` plus optional scripts, references, and assets. Its skill guidance emphasizes concise skills, progressive disclosure, appropriate degrees of freedom, and validation. Codex also supports `AGENTS.md` for durable repository guidance. citeturn1search1turn1search0

### Recommended setup

Install the orchestrator skill into the Codex skills directory used by your installation, for example:

```text
$CODEX_HOME/skills/auren/
```

If `CODEX_HOME` is not set, Codex normally uses its user-local configuration under the home directory. Prefer the official Codex skill installer or your existing skill-management mechanism rather than manually changing internal directories. Codex's official skill catalog supports installing skills from GitHub paths. citeturn1search3turn1search7

For a repository-specific setup, keep durable project rules in:

```text
AGENTS.md
```

and keep the orchestrator workflow in the skill. Codex documents `AGENTS.md` as a hierarchical instruction surface, while skills are intended for reusable workflows. citeturn1search0turn1search4

### Real Codex task

```text
$auren

Fix the intermittent duplicate-row bug in the tenant export pipeline.
Do not change the API contract.
First reproduce or establish the failure path, then identify the root cause.
Add a regression test and verify unaffected export modes.
```

If your Codex surface uses the `$skill-name` convention, invoke it as shown above. Codex's app-server interface also accepts an explicit skill input, which avoids skill-name resolution latency. citeturn1search5

### Codex review task

```text
$auren

Review the current diff for:
1. specification compliance
2. regression risk
3. weak architectural boundaries
4. data-model fragility
5. error handling
6. logging/metrics/tracing gaps
Do not modify files. Return actionable findings with evidence.
```

## 7. Gemini CLI

Gemini CLI supports the Agent Skills open standard. User skills can live under `~/.gemini/skills/` or `.agents/skills/`, while workspace skills can live under `.gemini/skills/` or `.agents/skills/`. Gemini provides `gemini skills install`, `link`, `enable`, `disable`, and `reload`. citeturn0search1turn0search5

### Install

For a Git repository skill:

```bash
gemini skills install https://github.com/sourabhJainR/AUREN.git
```

If the skill is in a subdirectory, use the appropriate `--path` supported by your Gemini CLI version. For development, linking is convenient:

```bash
gemini skills link ./skills/ai-coding-orchestrator
```

Then verify:

```text
/skills list
```

Gemini requires consent before activating a skill unless consent is explicitly configured. citeturn0search1turn0search5

### Real Gemini task

```text
Investigate the checkout timeout reported in JIRA-913.
Do not modify code yet.
Trace the request from API entry point through downstream calls.
Use repository evidence and distinguish facts from inference.
If the repository has an established telemetry pattern, use it in the analysis.
```

## 8. Other Agent Skills-compatible platforms

The core skill follows the Agent Skills shape: a directory containing `SKILL.md`, with optional references, scripts, and assets. This makes the workflow portable across hosts that implement the standard. Claude Code and Gemini CLI both document this model, and Codex has adopted the same structure. citeturn0search0turn0search1turn1search1

For an unsupported host:

1. Find its skill discovery directory.
2. Install/link `skills/ai-coding-orchestrator/` there.
3. Preserve the `SKILL.md` frontmatter required by that host.
4. Keep repository-specific rules in the host's native project instruction file.
5. Start with a read-only research/review task.
6. Confirm discovery and activation.
7. Run the deterministic repository evals.
8. Only then enable implementation workflows.

Do not assume every host supports automatic invocation, explicit slash commands, subagents, MCP, hooks, or permission controls. Use the host adapter only for capabilities it actually provides.

## 9. Using the system with code-mem and Graphify

These are optional evidence providers, not prerequisites.

Example:

```text
Investigate why changing the customer hierarchy causes stale report values.
Use the repository graph/memory capability if available.
Trace callers and downstream dependencies before proposing a fix.
Do not modify code.
```

Expected behavior:

```text
Task
 -> detect code-mem/Graphify availability
 -> query only relevant symbols/relationships
 -> rank evidence
 -> inspect source
 -> produce evidence-backed flow
```

If the provider is unavailable, continue with repository-native search and source inspection. Never fail the entire workflow because an optional provider is absent.

## 10. Using the system with Jira

A Jira issue can be supplied directly when the host has Jira access:

```text
Implement PROJ-1842.
Before coding, reconstruct the acceptance criteria and non-goals from the issue,
then inspect the repository for the existing implementation pattern.
Ask only questions that materially affect the implementation.
```

If Jira integration is unavailable, paste the issue or provide its exported text. Do not invent missing acceptance criteria.

## 11. The four common modes

### Implement

```text
Implement JIRA-4821. Preserve existing API behavior and add regression tests.
```

### Research

```text
Research the root cause of this latency problem. Do not modify code.
Use evidence and give a detailed fact/inference/unknown analysis.
```

### POC

```text
Build a disposable experiment to determine whether approach X meets Y.
Do not productionize it.
```

### Review / Grill

```text
Grill this PR. Challenge requirements, assumptions, architecture, data model,
operational behavior, observability, security and regression risk. Do not modify files.
```

## 12. Recommended first run on a new repository

Do not start with a large implementation task.

Run:

```text
Learn this repository enough to work safely.
Do not modify files.
Identify:
- project structure and architectural boundaries
- build/test commands
- exception/error-handling pattern
- logging and telemetry pattern
- dependency-management pattern
- testing conventions
- configuration and deployment conventions
- repository instructions
- likely high-risk areas
Return facts, evidence paths, unknowns, and the minimum context needed for future work.
```

Then run a small read-only review:

```text
Review one representative feature flow end-to-end.
Do not modify code.
Explain the entry point, important components, data flow, error paths,
tests, and observability using repository evidence.
```

Only after those checks should you begin significant implementation.

## 13. How to reduce back-and-forth

Give the system five things when known:

```text
Outcome
Constraints
Protected behavior
Acceptance criteria
Risk tolerance
```

For example:

```text
Outcome: add tenant-level export filtering.
Constraints: use existing authorization pattern; no new dependency.
Protected: existing API contract and non-tenant exports.
Acceptance: filtered rows are correct; existing export tests remain green.
Risk: medium; production API.
```

If you do not know these values, state what you know and let the orchestrator grill only the consequential gaps.

Avoid saying only:

```text
Make this better.
```

unless you intentionally want the system to begin in discovery/grill mode.

## 14. What the system should report at the end

For implementation:

```text
DONE
- what changed
- files/components changed
- tests run
- verification result

EVIDENCE
- important repository facts
- relevant test/command output

OPEN RISKS
- unresolved issues or unknowns

NEXT
- only if follow-up is genuinely needed
```

For research:

```text
QUESTION
EVIDENCE
FLOW / FINDINGS
FACTS
INFERENCES
UNKNOWNS
CONFIDENCE / LIMITATIONS
RECOMMENDATIONS
```

For review:

```text
CRITICAL
HIGH
MEDIUM
LOW

Each finding should contain evidence, impact, and a concrete recommendation.
```

## 15. Safe rollout

For a team rollout:

1. Start with read-only research and review.
2. Enable implementation on low-risk repositories.
3. Keep third-party extensions optional.
4. Run deterministic evals in CI.
5. Add a regression eval for every significant orchestrator defect.
6. Measure time-to-proven-change, model calls, tokens, retries, and verification failures.
7. Expand permissions only after the workflow is trusted.
8. Keep production deployment and merge authority outside the default agent permission boundary.

## 16. Troubleshooting

### Skill is not discovered

Check that:

- the directory is in the host's supported skill location;
- `SKILL.md` exists;
- required frontmatter is valid;
- the skill description clearly states when it should trigger;
- the host has refreshed/reloaded skills;
- the current repository is within the skill's discovery scope.

### It triggers too often

Narrow the `description`/trigger metadata. Keep always-loaded metadata short and move detailed procedures into references. Claude Code explicitly recommends concise skill bodies because loaded skill content remains in context; Gemini similarly uses metadata-first progressive disclosure. citeturn0search0turn0search1

### It asks too many questions

Tell it to proceed with bounded assumptions unless the missing answer materially changes correctness, safety, architecture, scope, or acceptance.

### It produces speculative research

Use:

```text
Separate facts, inferences, unknowns, and recommendations.
Cite the repository/source evidence for every material finding.
Do not modify code.
```

### An optional provider is unavailable

Continue without it. The core repository evidence path must remain functional.

## 17. Golden path

For most engineering tasks, this is enough:

```text
1. Start the agent in the repository.
2. Install/discover the orchestrator skill.
3. State the desired outcome in natural language.
4. Include constraints/protected behavior if known.
5. Let the orchestrator grill only consequential ambiguity.
6. Let it inspect repository conventions before changing code.
7. Let it implement the smallest safe change.
8. Require tests and regression verification.
9. Review architecture, operations and observability for meaningful changes.
10. Inspect DONE / EVIDENCE / OPEN RISKS before merging.
```

The user should not need to know which internal skill, graph provider, memory provider, model, or retrieval strategy was selected. That is the orchestrator's job.
