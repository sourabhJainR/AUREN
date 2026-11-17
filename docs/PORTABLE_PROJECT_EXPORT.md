# Portable project export

Run `auren export --project-root . --output ./auren-project-export.zip` to
create a portable snapshot of the current Git working tree and AUREN project
metadata.

The archive includes:
- Tracked and non-ignored untracked source files, subject to file/count/size limits.
- The sanitized `.auren/project.json` profile, when present.
- A project-scoped progress projection with task identity, lifecycle, next action
  and evidence references; raw prompts/messages are omitted.
- Digest-verified plan/run summaries from project-local plan stores.

The archive excludes Git internals, runtime databases, global user state, host
client configuration, credentials, token files, secret-bearing paths and
symlinks. Sensitive profile keys are redacted. Text files matching known secret
patterns are redacted before inclusion. Binary files are preserved only when
they are within the size budget; they are not scanned for embedded secrets.

The export is a snapshot, not a live connection to AUREN. Evidence references
are pointers and do not prove that evidence is available after export. The
source remains ordinary user-owned files and can be opened without AUREN.

Output is created exclusively with owner-only permissions. On Windows, the
current-user and SYSTEM ACL is applied and verified; on POSIX, mode 0600 is
applied. Existing output files are never overwritten. Limits: 5,000 files, 2 MiB per file and 64 MiB
total uncompressed package.
