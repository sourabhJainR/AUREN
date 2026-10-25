# Capability-aware execution

AUREN treats skills, MCP tools, plugins, providers, local workers and built-in
capabilities as optional execution resources. The runtime discovers what is
available, scores candidates for the current task, and keeps AUREN policy as the
authority for risk, network access, sandboxing, resource budgets, verification
and stopping.

## Runtime flow

```text
task
  -> declared capabilities
  -> installed/host-advertised discovery
  -> bounded capability selection
  -> historical pathway + resource arbitration
  -> execution
  -> evidence / verification
  -> learning
```

The selector is implemented by `portable.agent_capabilities.CapabilityExecutioner`.
It has no dependency on an MCP SDK, plugin SDK, external model, or provider.

## Optional discovery

Local skills are discovered from:

- `.claude/skills/<name>/SKILL.md`
- `.ai-harness/skills/<name>/SKILL.md`
- `skills/<name>/SKILL.md`
- paths listed in `AUREN_SKILLS_PATH`

MCP and plugin hosts can advertise bounded capability metadata through
`AUREN_MCP_CAPABILITIES` and `AUREN_PLUGIN_CAPABILITIES`. Each variable contains
a JSON array of descriptors. The runtime ignores malformed or unavailable
sources and continues with built-in capabilities.

Example:

```json
[
  {
    "name": "mcp:repo-search",
    "description": "repository semantic search",
    "tags": ["repository", "search"],
    "risk": "low",
    "estimated_latency_ms": 300,
    "estimated_cost": 0.2,
    "evidence_quality": 0.8,
    "confidence": 0.7
  }
]
```

Discovery only describes capabilities. It never grants permissions.

## Selection rules

Selection considers task fit, observed success, evidence quality, confidence,
latency, cost and resource demand. Historical outcomes are bounded so a
successful optional provider does not become a hard dependency.

Failed capabilities are excluded from the current acquisition. If an optional
path disappears or fails, the runtime falls back to remaining safe candidates.

Risk, network and sandbox gates always apply, including to explicitly requested
capabilities.

## Integration contract

Graph-agent execution creates the executioner once and supplies optional
discoverers when an integration has richer live metadata. Discoverers should
return `CapabilityOption` values and may fail safely. AUREN remains runnable when
all external discoverers are absent.

This is intentionally an adapter seam rather than a plugin framework. Existing
`CapabilityFabric`, `SkillRegistry`, `PathwayOptimizer`,
`HistoricalResourceRouter`, `LocalOffloadBroker`, memory and evidence
owners remain authoritative for their existing responsibilities.

## Verification

The capability selector is covered by `tests/test_agent_capabilities.py`,
including:

- deterministic selection with alternatives;
- failed optional-path fallback;
- risk/network/resource policy enforcement;
- bounded installed-skill discovery;
- graceful optional-discovery failure;
- required capability policy enforcement.



## Outcome calibration

Each graph-agent execution now records two learning indexes:

- the existing role/task outcome;
- an exact role/task/capability outcome.

The second index is consumed by future capability selection. This lets AUREN
learn that a particular skill, MCP tool, plugin or core capability worked or
failed for a task pattern without replacing the broader engineering history.

The capability-specific record includes outcome, evidence quality, cost,
duration and the selected capability. Failed observations remain usable as
negative evidence for the current bounded selection cycle, while promotion and
long-term learning continue through the existing learning steward and
verification paths.

This keeps capability learning evidence-backed and avoids turning a single
successful execution into a permanent preference.


## Bounded exploration

Capability history is used for exploitation, but AUREN also performs bounded
exploration. When a safe capability has no historical observation, the selector
may give one deterministic low-risk candidate a small exploration adjustment.
The candidate must still satisfy risk, network, sandbox, availability and
resource constraints.

Exploration is intentionally small and deterministic. It does not override a
required capability, a known failed capability, or the existing verification
and stopping policy. Once an outcome is recorded, subsequent selection can use
that evidence.


## Confidence-aware exploration

Capability selection now balances exploration and exploitation from observed sample count and confidence. Safe under-observed options receive a small bounded trial bonus; well-observed high-confidence options stop receiving that bonus. The exploration bonus is capped and cannot bypass risk, network, sandbox, required-capability, failure, or stopping policy.


## Collaborative skill selection

AUREN can evaluate multiple discovered skills as a bounded set instead of forcing a single winner. It starts from the strongest individual capability, then adds complementary skills when their marginal task coverage and evidence justify the bounded cost. This supports overlapping skills from different sources without requiring the user to name or coordinate them. Every member is independently subject to the existing risk, network, sandbox and failure policy.


## Invocation-aware skill discovery

AUREN reads a bounded provider-neutral subset of skill front matter: phase, tags,
provides, requires, model-invocable, risk and resource requirements.

This adapts the useful invocation boundary from Matt Pocock's skills. A skill
marked as human-only is retained as an available resource but is not selected by
the autonomous executioner. AUREN does not grant execution authority from skill
metadata. Dependencies and phases are inputs to selection and orchestration
only.

Malformed or missing metadata falls back to safe defaults, and external skill
providers remain optional.


## Skill bundle evaluation and graduation

Collaborative selection now evaluates bounded singleton, pair and triple bundles
instead of greedily appending skills. The candidate portfolio is capped, every
member is policy-checked, dependencies must be satisfiable, and redundant
skills are penalized.

Each bundle has an order-independent fingerprint. AUREN records bundle outcomes
separately from individual capability outcomes and reuses exact bundle history
for later selection.

Bundle states are intentionally conservative:

- experimental: insufficient evidence
- proven: repeated successful outcomes with adequate confidence
- degraded: repeated failures or weak success evidence, strongly penalized
- retired: repeated severe failure, excluded from autonomous selection

This gives AUREN the missing feedback loop: a combination can graduate when it
repeatedly adds value, while a combination that costs more without delivering
better evidence can fall out of the working set.

The design borrows the useful parts of Matt Pocock's workflow model: explicit
phase boundaries, composable skills, dependency-aware execution and feedback
from tests/review. AUREN keeps those concepts as data and policy inputs rather
than making the external skill framework a runtime dependency.


AUREN also honors the Codex skill sidecar at
`<skill>/agents/openai.yaml` when it declares
`policy.allow_implicit_invocation: false`. This keeps the autonomous boundary
consistent across Claude-style front matter and Codex metadata, while explicit
host invocation can still remain available outside the autonomous selector.


## Phase-aware bundle execution

A selected bundle now carries deterministic execution groups. Phase metadata
orders broad stages such as research, planning, implementation, verification
and review. Within a phase, skills can be combined; explicit dependency
metadata can force an edge.

The groups are advisory execution structure, not new authority. AUREN still
controls permissions, resource budgets, verification and stopping. Cyclic or
malformed dependency metadata falls back to a deterministic safe order rather
than blocking the task.


## Bounded collaborative execution

After bundle selection, AUREN converts phase/dependency groups into an execution
schedule. Independent members may share a group and are bounded by the
available parallelism. The schedule is deterministic and capped so adding
skills cannot create an unbounded worker fan-out.

The schedule is still subordinate to AUREN's resource, policy and verification
controls. It describes what can run together; it does not grant execution
authority.


## Durable inventory audit and cross-stage learning

Every graph-agent stage records a compact SQLite inventory snapshot at
`.auren/capability_audit.sqlite3`. It records bounded metadata for discovered
core capabilities, skills, MCP tools, plugins and any provider/API exposed by a
registered discoverer. Each snapshot is scoped to the run, stage and crew/role,
and uses descriptor digests to detect inventory changes. Discovery is
descriptive only; it never grants authority or overrides policy.

Selection decisions record the chosen capability, bounded alternatives,
digested request/rationale, outcome, evidence references, cost and duration.
Raw task text, skill instructions, credentials and provider payloads are not
written to this audit. Exact role/task learning remains the primary signal; a
capability-level cross-stage prior is consulted only when exact history is
absent, and the selector's existing confidence cap bounds its influence.
Outcomes are recorded separately for each stage/crew and for the shared
capability prior, so one crew's success cannot erase another crew's failures.

Use the existing discovery adapter seam for APIs and tools: an integration
must explicitly advertise bounded `CapabilityOption` metadata. AUREN does not
blindly enumerate credentials, import arbitrary packages, invoke unknown MCP
servers, or mutate/install plugins to discover them. The audit shows what was
advertised and selected, not that an external endpoint is trustworthy or that
its action succeeded; consequential execution still requires authority,
provider-bound receipts, independent verification and durable evidence.

Token economy: discover once per stage, cap the candidate portfolio, store
digests rather than prompts/instructions, use exact history before the shared
prior, and avoid rediscovering a source after its metadata is known to be fresh.
Do not reduce security checks or verification to save tokens.

Rollback: disable the audit writer only if durable storage is unavailable and
the task is non-consequential; consequential work must remain blocked if its
required audit cannot be persisted. Existing experience records remain the
learning source of truth; audit rows are an append-only explanation trail, not
a second policy or learning engine.


## Explicit user constraints take precedence over learned ranking

The collaborative selector recognizes exact named directives such as `must use
mcp:repo-search`, `use only skill:repo-review`, and `do not use
mcp:remote-write`. Explicit required options are included in the chosen bundle;
multiple requirements are combined only within the configured bundle limit.
An unavailable, excluded, over-limit, or policy-blocked requirement fails closed
instead of silently substituting a different tool. Explicit exclusions are
removed before scoring. Learned outcomes, cost and latency optimize only among
the remaining compliant choices.

This directive parser is intentionally conservative: it matches exact option
names (and their final namespaced component) after explicit directive phrases.
It does not infer permission from natural-language tool mentions, and no user
directive can override authority, network, sandbox, risk or verification gates.
