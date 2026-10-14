# AUREN State Migration Runbook

The migration helper copies and verifies state; it does not delete or rewrite the legacy source. Treat the retained source as the rollback copy until the new runtime has been validated and an operator explicitly decides how long to retain it.

## 1. Inspect first (default; no writes)

```bash
python scripts/migrate_auren_state.py --source ~/.aer/orchestration --destination ~/.auren/orchestration
```

Review the JSON result. It reports the manifest SHA-256, file count, byte count, and whether the destination is already migrated or conflicts with existing data. Dry-run must not create the destination.

## 2. Apply only after review

```bash
python scripts/migrate_auren_state.py --source ~/.aer/orchestration --destination ~/.auren/orchestration --apply
```

The operation acquires a sibling migration lock, copies to a staging directory, verifies the manifest and byte count, and atomically renames the staging directory into place. Symbolic links and unsupported entries fail closed. A conflicting destination is never overwritten.

## 3. Verify and rerun safely

Run the same command again with `--apply`. If the destination matches the source manifest, the result is `already_migrated_verified`; a mismatch is an error, not an overwrite. Keep the legacy source intact while validating the new installation.

## 4. Rollback

The migration tool never removes the legacy source. If the new runtime fails validation, stop the new runtime and point the operator configuration back to the preserved legacy root. Do not delete either root or attempt an automatic merge. If new writes have occurred under the canonical root, preserve both roots and reconcile the divergent state explicitly before switching back.

## Operational boundary

This tool verifies a filesystem copy and provides a non-destructive rollback path; it does not prove application-level schema compatibility, concurrent-writer safety across processes, or a production rollback rehearsal. Those require an operator-run validation with an explicit backup, last-known-good reference, and evidence receipt.
