# AUREN State Migration Runbook

The migration helper copies and verifies state; it does not delete or rewrite the legacy source. Treat the retained source as the rollback copy until the new runtime has been validated and an operator explicitly decides how long to retain it.

## 1. Inspect first (default; no writes)

```bash
python scripts/migrate_auren_state.py --source "$LEGACY_STATE_ROOT" --destination "$CANONICAL_STATE_ROOT"
```

Review the JSON result. It reports the manifest SHA-256, file count, byte count, and whether the destination is already migrated or conflicts with existing data. Dry-run must not create the destination.

## 2. Apply only after review

```bash
python scripts/migrate_auren_state.py --source "$LEGACY_STATE_ROOT" --destination "$CANONICAL_STATE_ROOT" --apply
```

The operation acquires a sibling migration lock, copies to a staging directory, verifies the manifest and byte count, and atomically renames the staging directory into place. Symbolic links and unsupported entries fail closed. A conflicting destination is never overwritten.

## 3. Verify and rerun safely

Run the same command again with `--apply`. If the destination matches the source manifest, the result is `already_migrated_verified`; a mismatch is an error, not an overwrite. Keep the legacy source intact while validating the new installation.

## 4. Rollback

The migration tool never removes the legacy source. If the new runtime fails validation, stop the new runtime and point the operator configuration back to the preserved legacy root. Do not delete either root or attempt an automatic merge. If new writes have occurred under the canonical root, preserve both roots and reconcile the divergent state explicitly before switching back.

## Operational boundary

This tool verifies a filesystem copy and provides a non-destructive rollback path; it does not prove application-level schema compatibility, concurrent-writer safety across processes, or a production rollback rehearsal. Those require an operator-run validation with an explicit backup, last-known-good reference, and evidence receipt.


## Capturing a staging rehearsal record

Use the helper only against an isolated staging backup and a new destination. It records file-level evidence and read-only SQLite `quick_check` results; it does not switch production configuration or claim application-level resume/rollback proof.

Dry-run first:

```bash
python -m portable.state_migration_rehearsal \
  --source /staging/legacy-state \
  --destination /staging/auren-state-candidate \
  --operator-id '<operator identity>' \
  --candidate-revision '<40-character candidate commit>' \
  --last-known-good-revision '<40-character last-known-good commit>' \
  --mode dry-run \
  --report /staging/evidence/migration-dry-run.json
```

After the operator approves the plan, copy into the isolated candidate root:

```bash
python -m portable.state_migration_rehearsal \
  --source /staging/legacy-state \
  --destination /staging/auren-state-candidate \
  --operator-id '<operator identity>' \
  --candidate-revision '<40-character candidate commit>' \
  --last-known-good-revision '<40-character last-known-good commit>' \
  --mode apply \
  --report /staging/evidence/migration-copy.json
```

The generated report deliberately sets `completion_claim_permitted=false`. It records manifest and SQLite checks only. The operator must separately attach immutable evidence references for fresh-process runtime resume, application/evidence-ledger readability, rollback to the preserved legacy root, and post-cutover write reconciliation. Do not mark issue #534 complete until those actions have been performed in staging and independently checked.
