"""Portable, secret-filtered project export without a live AUREN dependency.

The export contains Git working-tree source files, a sanitized project profile,
and allowlisted plan/progress/evidence references. Runtime databases, credentials
and global user state are never copied.
"""
from __future__ import annotations

import hashlib
import json
import stat
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile
from typing import Any, Mapping

from .chatgpt_handoff import SECRET_PATTERNS, SECRET_NAMES, redact
from .engineering_dashboard import EngineeringDashboard
from .progress_journal import ProgressJournal


SCHEMA_VERSION = "auren-project-export-v1"
MAX_FILES = 5000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
_SENSITIVE_KEY = re.compile(r"(?i)(?:secret|token|password|api[_-]?key|authorization|credential|private[_-]?key)")
_EXCLUDED_PARTS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".codex", ".claude", ".gemini", ".cursor", ".continue", ".credentials", ".secrets", "secrets"}
_EXCLUDED_NAMES = {name.lower() for name in SECRET_NAMES} | {".env", ".env.local", ".env.production"}


def _read_project_file(root: Path, relative: str) -> bytes:
    """Read a checkout file without following symlinked path components."""
    parts = PurePosixPath(relative).parts
    if not parts:
        raise ValueError("empty project path")
    if os.name == "nt" or not hasattr(os, "O_NOFOLLOW"):
        current = root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise PermissionError("project export refuses symlinked path components")
        resolved = current.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise PermissionError("project export path escapes the checkout")
        if not resolved.is_file():
            raise ValueError("project export source is not a regular file")
        return resolved.read_bytes()

    directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for part in parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise ValueError("project export source is not a regular file")
            with os.fdopen(file_fd, "rb") as stream:
                file_fd = -1
                return stream.read(MAX_FILE_BYTES + 1)
        finally:
            if file_fd >= 0:
                os.close(file_fd)
    except OSError as exc:
        if exc.errno in {getattr(__import__("errno"), "ELOOP", 40), getattr(__import__("errno"), "ENOTDIR", 20)}:
            raise PermissionError("project export refuses symlinked path components") from exc
        raise
    finally:
        os.close(directory_fd)


def _assert_safe_project_path(root: Path, relative: str) -> Path:
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise PermissionError("project export refuses symlinked path components")
    resolved = current.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise PermissionError("project export path escapes the checkout")
    return current


def _git_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root, check=True, capture_output=True, timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("unable to enumerate project files from Git") from exc
    paths = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("project contains a non-UTF-8 file path; export stopped") from exc
        path = PurePosixPath(value)
        if path.is_absolute() or "\\" in value or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("Git returned an unsafe project path")
        if any(part.lower() in _EXCLUDED_PARTS for part in path.parts):
            continue
        if any(part.lower() in _EXCLUDED_NAMES or part.lower().startswith(".env.") for part in path.parts):
            continue
        if path.parts and path.parts[0] == ".auren":
            # The project profile is re-emitted only after key-based sanitization;
            # progress and runtime stores are projected separately below.
            continue
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            continue
        paths.append(value)
    if len(paths) > MAX_FILES:
        raise ValueError("project file count exceeds the export limit")
    return sorted(set(paths))


def _sanitize(value: Any, *, key: str = "") -> Any:
    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact(value)[:12000]
    if isinstance(value, Mapping):
        return {str(name)[:128]: _sanitize(item, key=str(name)) for name, item in list(value.items())[:500]}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value[:500]]
    return "[UNSUPPORTED_VALUE]"


def _project_profile(root: Path) -> dict[str, Any]:
    path = _assert_safe_project_path(root, ".auren/project.json")
    if not path.exists():
        return {"status": "not_configured", "profile": None}
    if not path.is_file() or path.stat().st_size > 256 * 1024:
        raise ValueError("project profile is not a regular file under 256 KiB")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("project profile is invalid; export stopped") from exc
    if not isinstance(raw, dict):
        raise ValueError("project profile must be a JSON object")
    return {"status": "available", "profile": _sanitize(raw)}


def _progress_snapshot(root: Path) -> dict[str, Any]:
    path = _assert_safe_project_path(root, ".auren/progress.jsonl")
    if not path.exists():
        return {"status": "not_configured", "tasks": []}
    try:
        rows = ProgressJournal(path).list_snapshots(stale_after_seconds=60)
    except Exception as exc:
        return {"status": "unavailable", "reason": type(exc).__name__, "tasks": []}
    allowed = (
        "task_id", "state", "reported_state", "stage", "last_update",
        "seconds_since_update", "stale", "progress_percent", "completed_units",
        "total_units", "evidence_ref", "event_count", "priority", "parent_task_id",
        "decision_required", "next_action",
    )
    tasks = [{key: _sanitize(row.get(key), key=key) for key in allowed if key in row} for row in rows]
    return {"status": "available", "tasks": tasks}


def _plan_snapshot(root: Path) -> dict[str, Any]:
    try:
        _assert_safe_project_path(root, ".auren")
        dashboard = EngineeringDashboard(root)
        rows = dashboard._plan_runs(dashboard._sqlite_files())
    except Exception as exc:
        return {"status": "unavailable", "reason": type(exc).__name__, "plans": []}
    allowed = (
        "run_id", "status", "plan_digest", "source_revision", "revision",
        "task_counts", "evidence_count", "updated_at",
    )
    return {
        "status": "available",
        "plans": [{key: _sanitize(row.get(key), key=key) for key in allowed if key in row} for row in rows],
    }


def export_project_package(project_root: str | Path, output: str | Path) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("project root must be a directory")
    if not (root / ".git").exists():
        raise ValueError("project root must be a Git checkout")
    target = Path(output).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError("export output already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {}
    total = 0
    for relative in _git_files(root):
        try:
            raw = _read_project_file(root, relative)
        except FileNotFoundError:
            # A concurrently removed working-tree file is not exported.
            continue
        size = len(raw)
        if size > MAX_FILE_BYTES:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            # Preserve bounded binary project assets; never inspect as text.
            content = raw
        else:
            if any(pattern.search(text) for pattern in SECRET_PATTERNS):
                text = redact(text)
            content = text.encode("utf-8")
        total += len(content)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("project source exceeds the 64 MiB export budget")
        files[relative] = content

    profile = _project_profile(root)
    progress = _progress_snapshot(root)
    plans = _plan_snapshot(root)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "project_name": root.name,
        "project_profile": profile,
        "progress": progress,
        "plans": plans,
        "trust_boundary": (
            "Source files are user-owned content. Runtime databases, global user state, "
            "credentials, tokens and host-native configuration are excluded. Evidence "
            "references are pointers, not proof that the referenced evidence remains available."
        ),
        "files": [
            {"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in sorted(files.items())
        ],
    }
    manifest = json.dumps(metadata, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    manifest_bytes = manifest.encode("utf-8")
    total += len(manifest_bytes)
    if total > MAX_TOTAL_BYTES:
        raise ValueError("project export including manifest exceeds the 64 MiB budget")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w+b") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for name, content in sorted(files.items()):
                    info = zipfile.ZipInfo("project/" + name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o600 << 16
                    archive.writestr(info, content)
                info = zipfile.ZipInfo("auren-export.json", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, manifest_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            from .windows_acl import enforce_private_acl
            enforce_private_acl(target, directory=False)
        else:
            target.chmod(0o600)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {
        "status": "exported", "path": str(target),
        "file_count": len(files), "source_bytes": total,
        "project_profile": profile["status"], "progress": progress["status"],
        "plans": plans["status"], "sha256": digest,
        "schema_version": SCHEMA_VERSION,
    }


__all__ = ["export_project_package"]
