# Legacy Name and Compatibility Register

AUREN is the current product identity. New code, user-facing UI, active documentation, examples, and current operational guidance must use **AUREN**.

This register intentionally distinguishes compatibility from stale branding. Do not blindly rename a compatibility token when doing so would break an existing operator configuration or a test's intended input.

## Retained legacy references

| Reference | Classification | Required treatment |
|---|---|---|
| `AER_*` environment variables (including `AER_HOME`, `AER_CORE_BIN`, `AER_LOCAL_LLM_*`, and `AER_OBSERVABILITY`) | Legacy environment aliases | Canonical `AUREN_*` settings take precedence; legacy names are read only as fallback through `portable.identity_compat`. Do not introduce direct reads of legacy names in new runtime code. |
| `AER_GRAPH_TEAM` | Legacy graph/crew configuration alias | Canonical `AUREN_GRAPH_TEAM` takes precedence; the legacy alias is read only through the compatibility resolver. |
| `AER_CONTEXT_BROKER_TELEMETRY` | Legacy context-broker telemetry path | Canonical `AUREN_CONTEXT_BROKER_TELEMETRY` takes precedence; the legacy variable remains a fallback during the compatibility period. |
| `aer_execution_strategy`, `aer_execution_mode` in old graph checkpoints | Legacy serialized state keys | Read only as fallback when the corresponding `auren_*` key is absent; new graph state writes canonical `auren_*` keys. |
| `aer_adaptive_policy` in older task contexts | Legacy serialized/runtime context key | Read only as fallback when `auren_adaptive_policy` is absent; all new contexts write `auren_adaptive_policy`. |
| `aer_cli.py`, `aer-portable.zip`, `aer-bundle.json`, `aer-core` | Retired entry-point, distribution, manifest, and native-worker names | Use `auren_cli.py`, `auren-portable.zip`, `auren-bundle.json`, and `auren-core` for current installs. Mention old names only for migration or historical artifacts. |
| AER release names in `docs/LEGACY_LINEAGE_CONFORMANCE.md` | Historical lineage | Keep only as a historical release identifier. Do not present it as the current product name or current install path. |
| `AER-42` inside `scripts/conformance_oracles.py` | Adversarial test fixture | Keep as arbitrary input/output test data for normalization behavior, not as a product identity. |
| `AER_HOME` and existing `~/.aer` / project `.aer` state | Legacy state location | `AUREN_HOME` and `~/.auren` are canonical for new installs. Existing legacy state is copied by `AurenStatePaths` only with validation; source data remains for rollback. |
| `AER_LOCAL_LLM_*`, `AER_NETWORK_ALLOWED`, `AER_OBSERVABILITY`, `AER_SKILLS_PATH`, `AER_MCP_CAPABILITIES`, `AER_PLUGIN_CAPABILITIES`, `AER_CORE_BIN`, `AER_CONTEXT_BUDGET_CHARS`, `AER_STRATEGY`, `AER_TASK_ID` | Legacy configuration aliases | Canonical `AUREN_*` names take precedence; migrated resolvers read legacy values only when the canonical setting is absent. |
| `AER_SANDBOX`, `AER_SANDBOX_NETWORK`, `AER_COMPACTION_DIGEST`, and fallback diagnostic marker | Legacy provider/policy protocol markers | Canonical `AUREN_*` markers are emitted alongside legacy markers while downstream consumers migrate. |
| `AER_CONFORMANCE_OK`, `AER_CONFORMANCE_PROBE` and similar oracle sentinels | Test protocol fixtures | Keep only where test/provider contracts explicitly expect the sentinel. |
| Old names in explicitly archived release evidence | Historical evidence | Preserve for provenance; label as historical and link to the current canonical contract where useful. |

## Change policy

- New product references must use AUREN.
- Rename active module, CLI, environment, storage, or install identifiers only with a compatibility plan, regression tests, and rollback/forward-recovery.
- Do not rename a public import or persisted state path based on text search alone.
- The active-identity audit test is intentionally scoped to current runtime/UI/operational docs; historical evidence and compatibility tokens are governed by this register.
- Review this register whenever a legacy alias is removed or a historical artifact is migrated.


## Safe state migration

For the operator-run dry-run/apply/rollback sequence, see [State Migration Runbook](STATE_MIGRATION_RUNBOOK.md) and `python scripts/migrate_auren_state.py --help`.
