# Capability-aware execution

AER treats skills, MCP tools, plugins, providers, local workers and built-in
capabilities as optional execution resources. The runtime discovers what is
available, scores candidates for the current task, and keeps AER policy as the
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
- paths listed in `AER_SKILLS_PATH`

MCP and plugin hosts can advertise bounded capability metadata through
`AER_MCP_CAPABILITIES` and `AER_PLUGIN_CAPABILITIES`. Each variable contains
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
return `CapabilityOption` values and may fail safely. AER remains runnable when
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

The second index is consumed by future capability selection. This lets AER
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

Capability history is used for exploitation, but AER also performs bounded
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

AER can evaluate multiple discovered skills as a bounded set instead of forcing a single winner. It starts from the strongest individual capability, then adds complementary skills when their marginal task coverage and evidence justify the bounded cost. This supports overlapping skills from different sources without requiring the user to name or coordinate them. Every member is independently subject to the existing risk, network, sandbox and failure policy.


## Invocation-aware skill discovery

AER reads a bounded provider-neutral subset of skill front matter: phase, tags,
provides, requires, model-invocable, risk and resource requirements.

This adapts the useful invocation boundary from Matt Pocock's skills. A skill
marked as human-only is retained as an available resource but is not selected by
the autonomous executioner. AER does not grant execution authority from skill
metadata. Dependencies and phases are inputs to selection and orchestration
only.

Malformed or missing metadata falls back to safe defaults, and external skill
providers remain optional.
