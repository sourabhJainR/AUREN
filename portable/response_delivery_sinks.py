"""Atomic CLI output contracts for AUREN response delivery.

stdout is an atomic best-effort stream write with an explicit unknown-outcome
failure if the host accepts only a prefix. File output uses a same-directory
temporary file and atomic link/replace; neither path truncates oversized output.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
from typing import BinaryIO


class OutputDeliveryError(RuntimeError):
    """Output could not be committed under the requested delivery contract."""


class OutputOutcomeUnknown(OutputDeliveryError):
    """stdout may contain a prefix; automatic replay is not safe."""


def _validate(payload: bytes, max_bytes: int) -> str:
    if not isinstance(payload, bytes):
        raise TypeError("delivery payload must be bytes")
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    if len(payload) > max_bytes:
        raise OutputDeliveryError("output exceeds configured byte budget; no output was written")
    return hashlib.sha256(payload).hexdigest()


def deliver_stdout(payload: bytes, *, stream: BinaryIO, max_bytes: int = 8_000_000) -> dict[str, object]:
    """Write a fully validated payload to stdout; never split or retry a partial write."""
    digest = _validate(payload, max_bytes)
    try:
        written = stream.write(payload)
        stream.flush()
    except (BrokenPipeError, OSError) as exc:
        raise OutputOutcomeUnknown("stdout write outcome is unknown; do not replay automatically") from exc
    if written != len(payload):
        raise OutputOutcomeUnknown("stdout accepted only a prefix; output outcome is unknown")
    return {"mode": "stdout-atomic", "bytes": len(payload), "sha256": digest, "complete": True}


def deliver_file(
    payload: bytes, destination: str | Path, *, max_bytes: int = 8_000_000,
    overwrite: bool = False,
) -> dict[str, object]:
    """Commit a file atomically without exposing partial content at the destination."""
    digest = _validate(payload, max_bytes)
    requested = Path(destination).expanduser()
    if requested.is_symlink():
        raise OutputDeliveryError("refusing to replace a symlink output path")
    target = requested.resolve()
    parent = target.parent
    if not parent.is_dir():
        raise OutputDeliveryError("output directory does not exist")
    if target.exists() and not overwrite:
        raise FileExistsError(target)
    fd, temp_name = tempfile.mkstemp(prefix=".auren-delivery-", dir=parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temp, target)
        else:
            # link() is an atomic create-if-absent operation on the same filesystem.
            os.link(temp, target)
            temp.unlink()
        try:
            dir_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # File content and directory entry are committed; directory fsync is
            # best-effort on platforms that do not support opening directories.
            pass
    except FileExistsError:
        raise
    except OSError as exc:
        raise OutputDeliveryError("file output could not be committed atomically") from exc
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    return {"mode": "file-atomic", "path": str(target), "bytes": len(payload), "sha256": digest, "complete": True}


__all__ = ["OutputDeliveryError", "OutputOutcomeUnknown", "deliver_stdout", "deliver_file"]
