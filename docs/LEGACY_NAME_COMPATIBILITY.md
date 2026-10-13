# Legacy Name and Compatibility Register

AUREN is the current product identity. New code, user-facing UI, active documentation, examples, and current operational guidance must use **AUREN**.

This register intentionally distinguishes compatibility from stale branding. Do not blindly rename a compatibility token when doing so would break an existing operator configuration or a test's intended input.

## Retained legacy references

| Reference | Classification | Required treatment |
|---|---|---|
| `AUREN_GRAPH_TEAM` | Legacy environment-variable alias | Keep only as a documented fallback while `AUREN_GRAPH_TEAM` is canonical. Test canonical precedence and legacy fallback; do not use the legacy alias in new examples. |
| AER release names in `docs/LEGACY_LINEAGE_CONFORMANCE.md` | Historical lineage | Keep only as a historical release identifier. Do not present it as the current product name or current install path. |
| `AER-42` inside `scripts/conformance_oracles.py` | Adversarial test fixture | Keep as arbitrary input/output test data for normalization behavior, not as a product identity. |
| Old names in explicitly archived release evidence | Historical evidence | Preserve for provenance; label as historical and link to the current canonical contract where useful. |

## Change policy

- New product references must use AUREN.
- Rename active module, CLI, environment, storage, or install identifiers only with a compatibility plan, regression tests, and rollback/forward-recovery.
- Do not rename a public import or persisted state path based on text search alone.
- The active-identity audit test is intentionally scoped to current runtime/UI/operational docs; historical evidence and compatibility tokens are governed by this register.
- Review this register whenever a legacy alias is removed or a historical artifact is migrated.
