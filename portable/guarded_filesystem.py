"""Root-confined filesystem operations for AUREN workspace adapters.

POSIX operations use directory descriptors and O_NOFOLLOW for each path
component to reduce symlink and TOCTOU exposure. The Windows fallback performs
canonical containment and symlink checks but is not a kernel-level sandbox;
production isolation must still be supplied by the configured OS sandbox.
"""
from __future__ import annotations

import errno
import os
import stat
from pathlib import Path, PurePosixPath
from typing import BinaryIO, TextIO


def _relative_parts(relative_path: str | Path) -> tuple[str, ...]:
    if not isinstance(relative_path, (str, Path)):
        raise ValueError("workspace path must be a relative string or Path")
    raw = str(relative_path)
    if not raw or "\x00" in raw:
        raise ValueError("workspace path must be non-empty and contain no NUL")
    normalized = raw.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if candidate.is_absolute() or (candidate.parts and ":" in candidate.parts[0]):
        raise PermissionError("absolute workspace paths are forbidden")
    parts = candidate.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise PermissionError("workspace path traversal is forbidden")
    return tuple(parts)


def resolve_workspace_path(root: str | Path, relative_path: str | Path) -> Path:
    """Resolve a workspace-relative path and reject existing symlink components."""
    workspace = Path(root).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise NotADirectoryError("workspace root must be a directory")
    parts = _relative_parts(relative_path)
    current = workspace
    for index, part in enumerate(parts):
        current = current / part
        if current.is_symlink():
            raise PermissionError("workspace symlink traversal is forbidden")
        if index < len(parts) - 1 and current.exists() and not current.is_dir():
            raise NotADirectoryError("workspace parent component is not a directory")
    resolved = current.resolve(strict=False)
    if resolved == workspace or workspace not in resolved.parents:
        raise PermissionError("workspace path escapes the declared root")
    return resolved


def _open_posix(root: Path, parts: tuple[str, ...], mode: str, create_parents: bool) -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise PermissionError("race-resistant workspace file access is unavailable on this platform")
    writing = any(flag in mode for flag in ("w", "a", "x"))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent_fd = os.open(root, flags)
    try:
        for part in parts[:-1]:
            try:
                child_fd = os.open(part, flags, dir_fd=parent_fd)
            except FileNotFoundError:
                if not create_parents or not writing:
                    raise
                os.mkdir(part, mode=0o700, dir_fd=parent_fd)
                child_fd = os.open(part, flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = child_fd

        if mode in {"r", "rt"}:
            file_flags = os.O_RDONLY
        elif mode == "rb":
            file_flags = os.O_RDONLY
        elif mode in {"w", "wt"}:
            file_flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        elif mode == "wb":
            file_flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        elif mode in {"a", "at"}:
            file_flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        elif mode == "ab":
            file_flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        elif mode in {"x", "xt"}:
            file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        elif mode == "xb":
            file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        else:
            raise ValueError("unsupported workspace file mode")
        descriptor = os.open(parts[-1], file_flags | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        if not writing and not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise PermissionError("workspace file must be a regular file")
        return descriptor
    finally:
        os.close(parent_fd)


def open_workspace_file(
    root: str | Path,
    relative_path: str | Path,
    mode: str = "r",
    *,
    create_parents: bool = False,
) -> BinaryIO | TextIO:
    """Open a workspace file while refusing absolute paths and symlink traversal."""
    workspace = Path(root).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise NotADirectoryError("workspace root must be a directory")
    parts = _relative_parts(relative_path)
    if os.name == "posix":
        try:
            descriptor = _open_posix(workspace, parts, mode, create_parents)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise PermissionError("symlink traversal is forbidden") from exc
            raise
        return os.fdopen(descriptor, mode) if "b" in mode else os.fdopen(descriptor, mode, encoding="utf-8")
    target = resolve_workspace_path(workspace, relative_path)
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
        target = resolve_workspace_path(workspace, relative_path)
    return target.open(mode) if "b" in mode else target.open(mode, encoding="utf-8")


def read_workspace_text(root: str | Path, relative_path: str | Path) -> str:
    with open_workspace_file(root, relative_path, "r") as stream:
        return stream.read()


def read_workspace_bytes(root: str | Path, relative_path: str | Path) -> bytes:
    with open_workspace_file(root, relative_path, "rb") as stream:
        return stream.read()


def write_workspace_text(root: str | Path, relative_path: str | Path, content: str) -> Path:
    if not isinstance(content, str):
        raise TypeError("workspace text content must be a string")
    with open_workspace_file(root, relative_path, "w", create_parents=True) as stream:
        stream.write(content)
        stream.flush()
        if hasattr(stream, "fileno"):
            os.fsync(stream.fileno())
    return resolve_workspace_path(root, relative_path)


__all__ = [
    "open_workspace_file",
    "read_workspace_bytes",
    "read_workspace_text",
    "resolve_workspace_path",
    "write_workspace_text",
]
