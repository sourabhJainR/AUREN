# Operator-run state migration and rollback rehearsal

This is an acceptance gate, not a unit-test substitute. Do not close issue #534 or delete/overwrite the legacy state root until a named operator has completed this runbook and attached the evidence record.

## Preconditions

- Use an isolated staging host or disposable staging instance, never production for the first rehearsal.
- Take a consistent backup of the legacy state root while the candidate runtime is stopped or otherwise quiesced. Record the backup location and hash.
- Record operator identity, UTC start time, candidate revision, last-known-good revision, host/instance identifier, source root, and destination root.
- Confirm the destination is a new path. Never point it inside the legacy root or at a path containing state that must be retained.
- Keep the legacy root and backup read-only to the candidate runtime where feasible. Do not delete them as part of this exercise.

## 1. Dry-run and baseline evidence

Run from the repository root in the same Python environment as the candidate runtime:

```bash
python - <<'PY' | tee migration-dry-run.json
import json
from portable.state_migration import inspect_state_migration, migrate_state_root
source = "/absolute/path/to/legacy-state"
destination = "/absolute/path/to/candidate-state"
plan = inspect_state_migration(source, destination)
result = migrate_state_root(source, destination, dry_run=True)
print(json.dumps({
    "phase": "dry-run",
    "status": result.status,
    "source": str(result.source),
    "destination": str(result.destination),
    "manifest_sha256": result.manifest_sha256,
    "entry_count": result.file_count,
    "byte_count": result.byte_count,
    "destination_absent": not result.destination.exists(),
}, indent=2, sort_keys=True))
assert result.status == "dry_run"
assert not result.destination.exists()
PY
```

Review and retain the JSON output. Stop if the source path is unexpected, the destination already exists, or the dry-run changes either root.

## 2. Apply into the isolated destination

Stop/quiesce the candidate runtime before applying. Do not run two migrations concurrently.

```bash
python - <<'PY' | tee migration-apply.json
import json
from portable.state_migration import inspect_state_migration, migrate_state_root
source = "/absolute/path/to/legacy-state"
destination = "/absolute/path/to/candidate-state"
before = inspect_state_migration(source, destination)
result = migrate_state_root(source, destination, dry_run=False)
source_after = inspect_state_migration(source, source + "/.manifest-probe-never-created")
destination_after = inspect_state_migration(destination, destination + "/.manifest-probe-never-created")
print(json.dumps({
    "phase": "apply",
    "status": result.status,
    "source": str(result.source),
    "destination": str(result.destination),
    "source_manifest_sha256": source_after.manifest_sha256,
    "destination_manifest_sha256": destination_after.manifest_sha256,
    "source_entry_count": source_after.file_count,
    "destination_entry_count": destination_after.file_count,
    "source_byte_count": source_after.byte_count,
    "destination_byte_count": destination_after.byte_count,
    "source_retained": result.source.is_dir(),
}, indent=2, sort_keys=True))
assert result.status in {"copied_verified_source_retained", "already_migrated_verified"}
assert source_after.manifest_sha256 == destination_after.manifest_sha256
assert source_after.file_count == destination_after.file_count
assert source_after.byte_count == destination_after.byte_count
assert result.source.is_dir()
PY
```

The migration helper verifies the copied manifest before switching the destination into place. Independently compare the retained source and destination manifests and counts; do not rely only on the helper's status. Keep both roots.

## 3. Restart candidate and verify runtime state

1. Point only the isolated candidate runtime at the destination state root.
2. Start a new process (not a reused interpreter or in-memory supervisor).
3. Exercise a known staging session and a graph execution that was present before migration. Confirm the supervisor resumes from the persisted checkpoint and the graph can continue without replaying completed side effects.
4. Verify saved sessions, handoff/retry checkpoints, and evidence-ledger records can be read through the runtime's normal APIs.
5. Run SQLite integrity checks against every SQLite database in the migrated root:

```bash
find /absolute/path/to/candidate-state -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -print
```

For each database, capture the output of:

```sql
PRAGMA integrity_check;
```

Every database must return `ok`. Capture process start time, candidate revision, session/checkpoint identifiers used for the test, expected versus observed resume result, evidence-ledger read result, database paths, and integrity outputs. A process that merely starts is not a successful restart test.

## 4. Exercise rollback

1. Stop the candidate runtime cleanly.
2. Restore the staging configuration to the last-known-good revision and the preserved legacy state root.
3. Start a fresh process using that configuration.
4. Verify the same known session/checkpoint is readable and the legacy runtime behaves as expected.
5. Compare the legacy source manifest against the baseline from step 1. If the candidate wrote new state after cutover, explicitly reconcile it; do not silently discard it or claim the rollback is lossless.
6. Confirm both state roots still exist and retain the destination until evidence is accepted.

Rollback passes only if the previous runtime starts from the preserved source, the source's original bytes are unchanged (or every intentional post-cutover write is accounted for), and no state is lost without an explicit disposition.

## 5. Evidence required to close issue #534

Attach one signed/owned record containing:

- Operator identity and UTC timestamps for each phase.
- Staging host/instance identifier (exclude secrets and tokens).
- Candidate and last-known-good Git revisions.
- Source/destination paths, source backup reference, and baseline/final manifest SHA-256, entry count, and byte count.
- Dry-run and apply JSON outputs.
- Fresh-process restart outcome, identifiers for tested sessions/checkpoints, and evidence-ledger readability outcome.
- Every SQLite path and `PRAGMA integrity_check` output.
- Rollback startup outcome, post-cutover write reconciliation, and final source/destination existence checks.
- Links or retained artifact references for logs and outputs, plus explicit pass/fail per step.

Redact credentials and private customer data. A repository CI run or a synthetic unit test is not operator evidence. If any step fails, leave issue #534 open, keep the legacy state intact, capture the failure, and repair/rehearse again before promotion.
