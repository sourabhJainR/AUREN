"""Explicit, non-destructive state-root activation and rollback.

This helper never deletes either state root. The active-root pointer is updated
atomically only after the selected root's manifest matches the operator-approved
digest. Operators must stop writers or use their deployment's fencing mechanism
before switching roots; this module cannot fence external processes.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

from portable.state_migration import _manifest


def activate_state_root(root: str | Path, pointer_file: str | Path, *, expected_manifest_sha256: str) -> str:
    selected = Path(root).expanduser().resolve()
    pointer = Path(pointer_file).expanduser().resolve()
    _, actual, _ = _manifest(selected)
    if actual != expected_manifest_sha256:
        raise ValueError("selected state root does not match approved manifest digest")
    pointer.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{pointer.name}.", suffix=".tmp", dir=pointer.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(str(selected) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, pointer)
        try:
            directory_fd = os.open(pointer.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(selected)


def rollback_state_root(previous_root: str | Path, pointer_file: str | Path, *, expected_manifest_sha256: str) -> str:
    """Atomically point back to a verified prior root without deleting new state."""
    return activate_state_root(previous_root, pointer_file, expected_manifest_sha256=expected_manifest_sha256)


__all__ = ["activate_state_root", "rollback_state_root"]
