# Legacy lineage conformance

This document records runtime contracts inherited from superseded implementation PRs.

| Historical PR | Capability lineage | Current canonical surface |
|---|---|---|
| #29 | Hermes unified runtime | `portable/hermes_runtime.py`, capability fabric |
| #30 | Hermes control-plane integration | capability fabric, persistent memory, scheduler, provider routing |
| #31 | Hermes-inspired capabilities | `portable/agent_capabilities.py`, skills/memory/delegation |
| #65 | Agency Runtime v8 | `portable/ai_coding_agency_bridge.py`, Agency runtime modules |
| #84 | AER 22.0.0 portable distribution | current portable bundle/release lifecycle; versioned beyond 22.0.0 |
| #129 | LLM adaptive runtime trigger | `portable/adaptive_trigger.py`, `portable/trigger_runtime.py` |

## Contract

These PRs are historical lineage, not merge candidates. Their behavior must remain
available through the current canonical implementations. This test suite checks
behavioral contracts instead of requiring old branch files or exact historical
versions.

The suite is intentionally dependency-light and does not require an external model,
provider, Ollama instance, network service, or GitHub state.

If a future refactor removes one of these capabilities, the conformance suite should
fail before the change can be treated as a safe cleanup.
