# Safe legacy state-root migration

This helper is an explicit operator tool, not an automatic startup migration.
Runtime startup must continue to preserve and read legacy state until an operator
chooses to migrate it.

## Contract

- Run `inspect_state_migration(source, destination)` first and retain the
  returned manifest digest, entry count, and byte count in the change record.
- Run `migrate_state_root(source, destination, dry_run=True)` to rehearse the
  operation. This does not write either state root.
- Stop writers to the source before a real copy. Run with
  `dry_run=False` only after a backup and maintenance window are confirmed.
- The helper rejects symbolic links and unsupported entries, copies into a
  temporary sibling directory, compares content manifests, and renames the
  verified copy into the destination. It refuses a conflicting destination.
- The legacy source is retained. Do not delete it automatically; keep it until
  application-level checkpoint/resume and rollback checks pass.
- An identical destination is accepted on a repeated run. A different
  destination is a hard conflict and is never overwritten.
- A migration lock file prevents cooperating migration processes from racing.
  If a process is terminated while holding the lock, inspect the source,
  destination, and lock before removing the stale lock manually.

## Rollback

The helper deliberately does not delete or overwrite either state root. To
roll back, stop the application, preserve the current destination as a separate
backup, restore the previous configured state-root setting, and restart using
the retained legacy source. Verify task/checkpoint counts and application-level
resume before resuming writes. Record the exact source/destination digests and
the operator who performed the rollback.

## Limits

A matching file manifest proves copied-file equality, not application-level
database consistency, checkpoint schema compatibility, or successful task
resume. Quiesce writers and run the application-specific integrity checks and
restart/resume rehearsal before treating a migration as complete. This helper
is not yet wired into automatic runtime startup or a CLI command.
