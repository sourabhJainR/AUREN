"""Verified, non-destructive migration helpers for legacy AUREN state roots.

Migration copies state into a staging directory, verifies a deterministic
manifest, then switches the verified copy into place. The legacy source is
never deleted or modified.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterable


@dataclass(frozen=True)
class MigrationResult:
    source: Path
    destination: Path
    status: str
    manifest_sha256: str | None = None
    file_count: int = 0
    byte_count: int = 0


def _manifest(root: Path) -> tuple[tuple[tuple[str, str, int], ...], str, int]:
    if not root.is_dir():
        raise ValueError(f"state root is not a directory: {root}")
    entries: list[tuple[str, str, int]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f"state migration refuses symbolic links: {relative}")
        if path.is_dir():
            entries.append((relative + "/", "directory", 0))
            continue
        if not path.is_file():
            raise ValueError(f"state migration encountered unsupported entry: {relative}")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        entries.append((relative, digest.hexdigest(), size))
        total_bytes += size
    canonical = "\n".join(f"{name}\0{digest}\0{size}" for name, digest, size in entries)
    return tuple(entries), hashlib.sha256(canonical.encode("utf-8")).hexdigest(), total_bytes


def inspect_state_migration(source: str | Path, destination: str | Path) -> MigrationResult:
    """Inspect a migration without changing either path."""
    src, dst = Path(source).expanduser().resolve(), Path(destination).expanduser().resolve()
    if not src.is_dir():
        raise ValueError(f"legacy state root does not exist: {src}")
    _, digest, total = _manifest(src)
    if dst.exists():
        return MigrationResult(src, dst, "destination_exists", digest, len(_manifest(src)[0]), total)
    return MigrationResult(src, dst, "ready", digest, len(_manifest(src)[0]), total)


def migrate_state_root(
    source: str | Path,
    destination: str | Path,
    *,
    dry_run: bool = True,
) -> MigrationResult:
    """Copy and verify legacy state; retain the source as rollback material.

    Existing destinations are never overwritten. A dry run only returns the
    verified source manifest. The destination must not be inside the source.
    """
    plan = inspect_state_migration(source, destination)
    if plan.status == "destination_exists":
        raise FileExistsError(f"refusing to overwrite existing state root: {plan.destination}")
    if plan.source == plan.destination or plan.source in plan.destination.parents:
        raise ValueError("destination must not be the source or a child of the source")
    if plan.destination in plan.source.parents:
        raise ValueError("destination must not contain the legacy source")
    if dry_run:
        return MigrationResult(
            plan.source, plan.destination, "dry_run", plan.manifest_sha256,
            plan.file_count, plan.byte_count
        )

    parent = plan.destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging: Path | None = None
    try:
        staging = Path(tempfile.mkdtemp(prefix=f".{plan.destination.name}.migration-", dir=parent))
        staging.rmdir()
        shutil.copytree(plan.source, staging, copy_function=shutil.copy2, symlinks=True)
        _, copied_digest, copied_bytes = _manifest(staging)
        if copied_digest != plan.manifest_sha256 or copied_bytes != plan.byte_count:
            raise IOError("state migration verification failed; original state was preserved")
        # Recheck immediately before the final switch; never intentionally overwrite.
        if plan.destination.exists():
            raise FileExistsError(f"destination appeared during migration: {plan.destination}")
        os.rename(staging, plan.destination)
        staging = None
        _, final_digest, final_bytes = _manifest(plan.destination)
        if final_digest != plan.manifest_sha256 or final_bytes != plan.byte_count:
            # Preserve both copies and surface the discrepancy for operator recovery.
            raise IOError("destination verification failed after switch; inspect both state roots")
        return MigrationResult(
            plan.source, plan.destination, "copied_verified_source_retained",
            final_digest, plan.file_count, final_bytes
        )
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


__all__ = ["MigrationResult", "inspect_state_migration", "migrate_state_root"]
