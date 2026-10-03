# Hermes capability parity for AUREN

AUREN is not a fork of Hermes Agent. It adopts the strongest agent-runtime patterns from Hermes while keeping AUREN's repository-first engineering controls authoritative.

Hermes provides a broad autonomous-agent surface: multiple model providers, persistent memory and session recall, procedural skills, scheduled automation, delegation and parallel work, programmatic tool calling, MCP, multiple terminal backends, messaging gateways, voice/multimodal tools, security controls and research/trajectory workflows. The upstream project documents these as first-class capabilities. AUREN maps the useful engineering parts into a smaller, testable control plane. 

## Capability contract

| Capability | AUREN implementation | Policy owner |
|---|---|---|
| Provider/model switching | `portable.hermes_runtime.ProviderRouter` | AUREN provider contract |
| Provider capability discovery | `ProviderSpec.capabilities` | AUREN routing |
| Profiles | Profile-scoped durable state | AUREN session policy |
| Persistent memory | SQLite memories with confidence/source | AUREN learning policy |
| Cross-session recall | SQLite FTS5 message index | AUREN context policy |
| Skills/procedural memory | `SkillRegistry` plus `.agents/skills` | AUREN skill contract |
| Tool registry/toolsets | `ToolRegistry` with tags and risk | AUREN execution policy |
| Approval/security | `ApprovalPolicy`, fail closed | AUREN security policy |
| Local execution | `LocalExecutor` | AUREN sandbox boundary |
| Parallel delegation | `DelegationCoordinator` | AUREN graph/ownership rules |
| Durable sessions | `SessionState` + SQLite | AUREN recovery policy |
| Scheduled work | SQLite job records | AUREN workflow scheduler |
| MCP | adapter boundary | AUREN permission and verification rules |
| Messaging gateways | adapter boundary | external gateway implementations |
| Voice/multimodal | provider adapter boundary | model/provider capabilities |
| Docker/SSH/Modal/etc. | existing AUREN environment abstraction | AUREN sandbox policy |
| Verification and learning | existing AUREN verification, regression and promotion gates | AUREN, never model/provider |

## Design rule

Do not copy Hermes implementation wholesale. Keep the useful behavior and remove unnecessary coupling:

1. **AUREN owns the engineering contract.** Repository rules, permissions, acceptance criteria, verification and promotion gates cannot be weakened by a provider, skill or learned strategy.
2. **Providers are replaceable.** A provider exposes capabilities; it does not define task semantics.
3. **Tools are explicit.** Every tool has a name, description, risk level and approval requirement. MCP and gateway tools enter through the same registry boundary.
4. **Memory is evidence, not truth.** Stored memories have provenance and confidence. They are retrieved as context and never silently become repository facts.
5. **Skills are procedural memory.** Skills contain reusable instructions and are selected by relevance rather than injected wholesale into every prompt.
6. **Parallelism is bounded.** Independent read-only work may run concurrently. Shared mutations remain serialized and owned by one worker.
7. **Long-running work is resumable.** Session checkpoints retain stage, task, attempt, errors and state digest.
8. **Security fails closed.** Tool approval, sandbox policy and repository permissions are stronger than model preference or learned behavior.
9. **Quality is measured after execution.** Every meaningful task produces evidence, verification results, regression outcomes and open risks.

## Recommended execution pipeline

```text
User request
  -> Intent + acceptance contract
  -> Repository/context discovery
  -> Capability discovery
  -> Skill retrieval
  -> Memory/session recall
  -> Dependency-aware task graph
  -> Parallel read-only exploration
  -> Single-owner mutations
  -> Tool execution through policy boundary
  -> Observe / evaluate
  -> Focused verification
  -> Independent review
  -> Targeted repair
  -> Regression replay
  -> Evidence-backed outcome
  -> Learning candidate
  -> Shadow/canary promotion
```

## Quality gates

A task is not complete merely because the model returned a plausible answer. AUREN should require, where applicable:

- repository instructions inspected;
- current state and dependencies identified;
- acceptance criteria explicit;
- impact analysis performed for shared surfaces;
- edits limited to requested scope;
- deterministic checks executed;
- tests or equivalent verification reported;
- final diff reviewed;
- unresolved risks and missing checks listed;
- learned changes kept behind regression and promotion gates.

## Usage

The runtime primitives are intentionally dependency-free:

```python
from portable.hermes_runtime import (
    ProviderRouter, ProviderSpec, DurableStore,
    SkillRegistry, ToolRegistry, ApprovalPolicy,
    LocalExecutor, DelegationCoordinator,
)
```

Use these primitives from AUREN orchestration code. Do not make `hermes_runtime.py` the owner of AUREN policy. This separation keeps the implementation easier to test, replace and extend.

## Upstream reference

Hermes Agent is MIT licensed and documents its architecture and capabilities publicly. AUREN should track capability changes from upstream, but implement only the parts that improve repository engineering quality and can be protected by deterministic tests. See the upstream repository and documentation before adding new adapters.
