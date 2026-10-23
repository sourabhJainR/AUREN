# Portable bundle state boundary

The portable AUREN bundle contains executable source and reviewed configuration, not
mutable runtime state. Exclude SQLite database files (`.sqlite`, `.sqlite3`, `.db`,
and `.db3`) and SQLite WAL, SHM, and journal sidecars from both the bundle payload and
its integrity manifest.

SQLite sidecars can appear or disappear while tests or local services close database
connections. Treating them as source can make the bundle nondeterministic or leak local
state. Do not hide unrelated missing source files: required source files must still
fail bundle verification when absent.

## Rollback

Revert the exclusion code, regression test, and this document together. Preserve the
execution, learning, evidence, and progress databases in their original locations.
Never package or delete those stores as part of a code rollback.
