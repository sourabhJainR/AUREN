"""Read-only preflight plan for legacy state migration.

This module deliberately performs no writes. It reports which known files would
be copied or registered, detects conflicts that would stop the real migration,
and identifies unknown files that will be left untouched.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .state_paths import AurenStatePaths, StatePathConflict, _KNOWN_DATABASES, _KNOWN_FILES


def plan_state_migration(
    canonical_root: str | Path,
    legacy_root: str | Path,
) -> dict[str, Any]:
    """Return a deterministic migration plan without creating or changing files."""
    canonical_input = Path(canonical_root).expanduser()
    legacy_input = Path(legacy_root).expanduser()
    if legacy_input.is_symlink():
        raise StatePathConflict(
            f"Legacy state root is a symbolic link: {legacy_input}. "
            "Select and review the real source directory explicitly before migration."
        )
    if not legacy_input.is_dir():
        raise FileNotFoundError(f"Legacy state root does not exist or is not a directory: {legacy_input}")

    canonical = canonical_input.resolve()
    legacy = legacy_input.resolve()
    if legacy == canonical or legacy in canonical.parents or canonical in legacy.parents:
        raise ValueError("canonical and legacy state roots must be distinct, non-overlapping directories")

    for path in legacy.rglob("*"):
        if path.is_symlink():
            relative = path.relative_to(legacy).as_posix()
            raise StatePathConflict(
                f"Legacy state contains a symbolic link: {relative}. "
                "Resolve the link explicitly before migration."
            )

    paths = AurenStatePaths(canonical, migration_manifest=canonical / ".state-migration.json")
    manifest = paths._read_manifest()
    recorded = manifest["files"]
    candidates = sorted(
        path for path in legacy.rglob("*")
        if path.is_file() and (
            path.relative_to(legacy).as_posix() in _KNOWN_DATABASES
            or path.relative_to(legacy).as_posix() in _KNOWN_FILES
            or (path.parent.name == "sessions" and path.suffix.lower() == ".json")
        )
    )
    known_relative = {path.relative_to(legacy).as_posix() for path in candidates}
    unknown_files = sorted(
        path.relative_to(legacy).as_posix()
        for path in legacy.rglob("*")
        if path.is_file() and path.relative_to(legacy).as_posix() not in known_relative
    )

    items: list[dict[str, str]] = []
    conflicts: list[str] = []
    for source in candidates:
        relative = source.relative_to(legacy).as_posix()
        target = canonical / relative
        kind = "sqlite" if source.suffix.lower() in {".db", ".sqlite", ".sqlite3"} else "file"
        source_digest = paths._sqlite_digest(source) if kind == "sqlite" else paths._file_digest(source)
        entry: dict[str, str] = {"path": relative, "kind": kind, "source_sha256": source_digest}
        prior = recorded.get(relative)
        if prior is not None:
            if prior.get("kind") != kind or prior.get("source_digest") != source_digest:
                entry["action"] = "conflict"
                entry["reason"] = "legacy source changed after recorded migration"
                conflicts.append(relative)
            elif not target.is_file():
                entry["action"] = "conflict"
                entry["reason"] = "manifest records migration but canonical target is missing"
                conflicts.append(relative)
            else:
                entry["action"] = "already_migrated"
                entry["target_sha256"] = (
                    paths._sqlite_digest(target) if kind == "sqlite" else paths._file_digest(target)
                )
        elif target.exists():
            target_digest = paths._sqlite_digest(target) if kind == "sqlite" else paths._file_digest(target)
            entry["target_sha256"] = target_digest
            if target_digest != source_digest:
                entry["action"] = "conflict"
                entry["reason"] = "canonical and legacy state differ; neither would be overwritten"
                conflicts.append(relative)
            else:
                entry["action"] = "record_existing_identical"
        else:
            entry["action"] = "copy"
        items.append(entry)

    return {
        "version": 1,
        "canonical_root": str(canonical),
        "legacy_root": str(legacy),
        "safe_to_apply": not conflicts,
        "manifest_exists": paths.migration_manifest.is_file(),
        "items": items,
        "conflicts": conflicts,
        "unknown_files_left_untouched": unknown_files,
        "summary": {
            "copy": sum(item["action"] == "copy" for item in items),
            "record_existing_identical": sum(item["action"] == "record_existing_identical" for item in items),
            "already_migrated": sum(item["action"] == "already_migrated" for item in items),
            "conflict": len(conflicts),
            "unknown_left_untouched": len(unknown_files),
        },
    }


__all__ = ["plan_state_migration"]
