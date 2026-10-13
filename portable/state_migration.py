"""Explicit, non-destructive migration of legacy AUREN state directories."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def _manifest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not root.exists():
        return result
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"state migration does not follow symlinks: {path.relative_to(root)}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result[relative] = digest
    return result


def migrate_state_directory(
    source: str | Path,
    destination: str | Path,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Copy and verify state; preserve source; refuse to overwrite divergent data.

    A successful migration never deletes the legacy source. The caller may switch
    configuration only after checking the verified manifest. Rollback is selecting
    the untouched source directory again.
    """
    src = Path(source).expanduser().resolve()
    dst = Path(destination).expanduser().resolve()
    if src == dst:
        raise ValueError("source and destination must differ")
    if not src.is_dir():
        raise FileNotFoundError(f"legacy state source does not exist: {src}")
    source_manifest = _manifest(src)
    if dst.exists():
        destination_manifest = _manifest(dst)
        if source_manifest == destination_manifest:
            return {
                "status": "already_migrated",
                "dry_run": dry_run,
                "source": str(src),
                "destination": str(dst),
                "file_count": len(source_manifest),
                "manifest_digest": hashlib.sha256(repr(sorted(source_manifest.items())).encode()).hexdigest(),
                "source_preserved": True,
            }
        raise FileExistsError("destination exists with different content; refusing to overwrite")
    report = {
        "status": "planned" if dry_run else "verified",
        "dry_run": dry_run,
        "source": str(src),
        "destination": str(dst),
        "file_count": len(source_manifest),
        "manifest_digest": hashlib.sha256(repr(sorted(source_manifest.items())).encode()).hexdigest(),
        "source_preserved": True,
    }
    if dry_run:
        return report
    dst.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{dst.name}.migration-", dir=str(dst.parent)))
    try:
        shutil.rmtree(temp)
        shutil.copytree(src, temp, symlinks=True)
        copied_manifest = _manifest(temp)
        if copied_manifest != source_manifest:
            raise IOError("state migration verification failed: source and copy manifests differ")
        # Atomic directory publication. Destination must remain absent; never replace.
        os.rename(temp, dst)
        if _manifest(dst) != source_manifest:
            raise IOError("state migration verification failed after publication")
    except Exception:
        if temp.exists():
            shutil.rmtree(temp, ignore_errors=True)
        raise
    return report


__all__ = ["migrate_state_directory"]
