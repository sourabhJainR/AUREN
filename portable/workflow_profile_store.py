"""Crash-safe persistence for user-selected AUREN workflow profiles.

Profiles are preferences, never authorization grants. The execution controller
must continue to enforce repository policy, approvals, review, and CI gates.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .workflow_profile import WorkflowProfile, WorkflowProfileError

MAX_PROFILE_BYTES = 16 * 1024


class WorkflowProfileStoreError(ValueError):
    """Raised when a profile cannot be read or persisted safely."""


def _reject_symlink(path: Path) -> None:
    try:
        if path.is_symlink():
            raise WorkflowProfileStoreError(f"refusing symlink workflow profile: {path}")
    except OSError as exc:
        raise WorkflowProfileStoreError(f"cannot inspect workflow profile path: {path}") from exc


def _decode_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorkflowProfileStoreError(f"duplicate workflow profile field: {key}")
        result[key] = value
    return result


def load_profile(path: str | Path) -> WorkflowProfile | None:
    """Load a profile; return None only when no profile exists.

    Malformed, oversized, unreadable, or incompatible profiles raise an error.
    They are never silently replaced with defaults.
    """
    target = Path(path).expanduser()
    _reject_symlink(target)
    try:
        with target.open("rb") as handle:
            raw = handle.read(MAX_PROFILE_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise WorkflowProfileStoreError(f"cannot read workflow profile: {target}") from exc
    if len(raw) > MAX_PROFILE_BYTES:
        raise WorkflowProfileStoreError("workflow profile exceeds size limit")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_decode_object_pairs)
        return WorkflowProfile.from_mapping(value)
    except (UnicodeDecodeError, json.JSONDecodeError, WorkflowProfileError) as exc:
        raise WorkflowProfileStoreError(f"invalid workflow profile: {exc}") from exc


def save_profile(path: str | Path, profile: WorkflowProfile | Mapping[str, Any]) -> WorkflowProfile:
    """Atomically persist a validated profile with restrictive file permissions."""
    try:
        validated = profile if isinstance(profile, WorkflowProfile) else WorkflowProfile.from_mapping(profile)
    except WorkflowProfileError as exc:
        raise WorkflowProfileStoreError(f"refusing invalid workflow profile: {exc}") from exc

    target = Path(path).expanduser()
    _reject_symlink(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkflowProfileStoreError(f"cannot create workflow profile directory: {target.parent}") from exc
    _reject_symlink(target)

    payload = (
        json.dumps(validated.as_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_PROFILE_BYTES:
        raise WorkflowProfileStoreError("workflow profile exceeds size limit")

    fd = -1
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".workflow-profile-", suffix=".tmp", dir=target.parent)
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Recheck immediately before replacement; os.replace does not follow target symlinks.
        _reject_symlink(target)
        os.replace(temporary, target)
        temporary = None
        if os.name == "posix":
            dir_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except OSError as exc:
        raise WorkflowProfileStoreError(f"cannot persist workflow profile: {target}") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return validated


__all__ = ["MAX_PROFILE_BYTES", "WorkflowProfileStoreError", "load_profile", "save_profile"]
