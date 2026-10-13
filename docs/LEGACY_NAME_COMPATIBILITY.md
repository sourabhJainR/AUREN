| `AER_GRAPH_TEAM` | Legacy crew/graph configuration alias | Resolve through the canonical configuration resolver; new configuration and emitted state use `AUREN_GRAPH_TEAM`. |\n# Legacy Name and Compatibility Register

AUREN is the current product identity. New code, user-facing UI, active documentation, examples, and current operational guidance must use **AUREN**.

This register intentionally distinguishes compatibility from stale branding. Do not blindly rename a compatibility token when doing so would break an existing operator configuration or a test's intended input.

## Retained legacy references

| Reference | Classification | Required treatment |
|---|---|---|
| `AER_*` environment variables (including `AER_HOME`, `AER_CORE_BIN`, `AER_LOCAL_LLM_*`, and `AER_OBSERVABILITY`) | Legacy environment aliases | Canonical `AUREN_*` settings take precedence; legacy names are read only as fallback through `portable.identity_compat`. Do not introduce direct reads of legacy names in new runtime code. |
| `aer_execution_strategy`, `aer_execution_mode` in old graph checkpoints | Legacy serialized state keys | Read only as fallback when the corresponding `auren_*` key is absent; new graph state must write only canonical `auren_*` keys. |
| `aer_cli.py`, `aer-portable.zip`, `aer-bundle.json`, `aer-core` | Retired entry-point, distribution, manifest, and native-worker names | Use `auren_cli.py`, `auren-portable.zip`, `auren-bundle.json`, and `auren-core` for current installs. Mention old names only when documenting migration or historical artifacts. |
| AER release names in `docs/LEGACY_LINEAGE_CONFORMANCE.md` | Historical lineage | Keep only as a historical release identifier. Do not present it as the current product name or current install path. |
| `AER-42` inside `scripts/conformance_oracles.py` | Adversarial test fixture | Keep as arbitrary input/output test data for normalization behavior, not as a product identity. |
| Old names in explicitly archived release evidence | Historical evidence | Preserve for provenance; label as historical and link to the current canonical contract where useful. |

## Change policy

- New product references must use AUREN.
- Rename active module, CLI, environment, storage, or install identifiers only with a compatibility plan, regression tests, and rollback/forward-recovery.
- Do not rename a public import or persisted state path based on text search alone.
- The active-identity audit test is intentionally scoped to current runtime/UI/operational docs; historical evidence and compatibility tokens are governed by this register.
- Review this register whenever a legacy alias is removed or a historical artifact is migrated.
