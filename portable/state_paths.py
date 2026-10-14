"""Canonical AUREN machine-state paths with a reversible legacy compatibility migration.

Migration copies SQLite databases through SQLite's backup API (so WAL contents are
included) and copies session JSON atomically. Legacy files are retained. Conflicting
pre-existing stores fail closed rather than silently overwriting either version.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


class StatePathConflict(RuntimeError):
    """Raised when canonical and legacy state cannot be reconciled safely."""


_KNOWN_DATABASES = frozenset({
    "memory/memory.db",
    "automation/automation.db",
    "executive/.auren/execution.sqlite3",
    "executive/.auren/executive.sqlite3",
})

# Non-database durable files read or written by active runtime services.
# Keep this list explicit: unknown legacy files are never copied automatically.
_KNOWN_FILES = frozenset({
    "active.json",
    "history.jsonl",
    "observability/traces.jsonl",
    "orchestration/active.py",
    "orchestration/previous.py",
    "orchestration/promotion.jsonl",
})


class AurenStatePaths:
    """Resolve the canonical state root and safely copy legacy state when needed."""

    def __init__(self, root: Path, *, migration_manifest: Path) -> None:
        self.root = root
        self.migration_manifest = migration_manifest

    @classmethod
    def prepare(
        cls,
        canonical_root: str | Path | None = None,
        legacy_root: str | Path | None = None,
    ) -> "AurenStatePaths":
        root = Path(canonical_root or (Path.home() / ".auren")).expanduser().resolve()
        legacy_input = Path(legacy_root).expanduser() if legacy_root is not None else None
        if legacy_input is not None and legacy_input.is_symlink():
            raise StatePathConflict(
                f"Legacy state root is a symbolic link: {legacy_input}. "
                "Select and review the real source directory explicitly before migration."
            )
        legacy = legacy_input.resolve() if legacy_input is not None else None
        if legacy is not None and (legacy == root or legacy in root.parents or root in legacy.parents):
            raise ValueError("canonical and legacy state roots must be distinct, non-overlapping directories")
        root.mkdir(parents=True, exist_ok=True)
        paths = cls(root, migration_manifest=root / ".state-migration.json")
        if legacy is not None and legacy.is_dir():
            paths._migrate_legacy(legacy)
        return paths

    def _read_manifest(self) -> dict[str, Any]:
        if not self.migration_manifest.exists():
            return {"version": 1, "files": {}}
        try:
            value = json.loads(self.migration_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StatePathConflict(
                f"Cannot read state migration manifest {self.migration_manifest}; preserve both roots and repair it before restarting."
            ) from exc
        if value.get("version") != 1 or not isinstance(value.get("files"), dict):
            raise StatePathConflict(f"Unsupported state migration manifest: {self.migration_manifest}")
        return value

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".state-migration-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(manifest, stream, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.migration_manifest)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _sqlite_digest(path: Path) -> str:
        digest = hashlib.sha256()
        db = None
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
            for row in db.iterdump():
                digest.update(row.encode("utf-8"))
                digest.update(b"\n")
        except sqlite3.Error as exc:
            raise StatePathConflict(f"State database is not readable SQLite data: {path}") from exc
        finally:
            if db is not None:
                db.close()
        return digest.hexdigest()

    @staticmethod
    def _file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _migrate_legacy(self, legacy: Path) -> None:
        manifest = self._read_manifest()
        files: dict[str, Any] = manifest["files"]
        # Reject links anywhere in the legacy tree before copying anything. A
        # symlink at a known path could make the migration read outside the
        # selected state root; silently following it would violate the
        # non-destructive, operator-bounded migration contract.
        for path in legacy.rglob("*"):
            if path.is_symlink():
                relative = path.relative_to(legacy).as_posix()
                raise StatePathConflict(
                    f"Legacy state contains a symbolic link: {relative}. "
                    "Both roots are preserved; resolve the link explicitly before migration."
                )

        # Only migrate known durable data formats. Unknown legacy files remain untouched.
        candidates = sorted(
            path for path in legacy.rglob("*")
            if path.is_file() and (
                path.relative_to(legacy).as_posix() in _KNOWN_DATABASES
                or path.relative_to(legacy).as_posix() in _KNOWN_FILES
                or (path.parent.name == "sessions" and path.suffix.lower() == ".json")
            )
        )
        for source in candidates:
            relative = source.relative_to(legacy).as_posix()
            target = self.root / relative
            kind = "sqlite" if source.suffix.lower() in {".db", ".sqlite", ".sqlite3"} else "file"
            source_digest = self._sqlite_digest(source) if kind == "sqlite" else self._file_digest(source)
            recorded = files.get(relative)
            if recorded is not None:
                if recorded.get("kind") != kind or recorded.get("source_digest") != source_digest:
                    raise StatePathConflict(
                        f"Legacy state changed after migration: {source}. Both roots are preserved; reconcile this store explicitly."
                    )
                if not target.is_file():
                    raise StatePathConflict(
                        f"Migrated state is missing at {target}; restore it or explicitly recover from {source} before restarting."
                    )
                continue

            if target.exists():
                target_digest = self._sqlite_digest(target) if kind == "sqlite" else self._file_digest(target)
                if target_digest != source_digest:
                    raise StatePathConflict(
                        f"Both legacy and canonical state exist with different contents: {source} and {target}. "
                        "Neither copy was overwritten. Back up both roots and reconcile explicitly."
                    )
                files[relative] = {"kind": kind, "source_digest": source_digest}
                self._write_manifest(manifest)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            if kind == "sqlite":
                fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.migration-", suffix=".tmp", dir=target.parent)
                os.close(fd)
                try:
                    source_db = sqlite3.connect(source, timeout=10)
                    target_db = sqlite3.connect(temp_name, timeout=10)
                    try:
                        source_db.backup(target_db)
                        result = target_db.execute("PRAGMA integrity_check").fetchone()
                        if not result or result[0] != "ok":
                            raise StatePathConflict(f"SQLite integrity check failed while copying {source}")
                    finally:
                        target_db.close()
                        source_db.close()
                    if self._sqlite_digest(Path(temp_name)) != source_digest:
                        raise StatePathConflict(f"Copied SQLite state failed digest verification: {source}")
                    os.replace(temp_name, target)
                finally:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)
            else:
                fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.migration-", suffix=".tmp", dir=target.parent)
                os.close(fd)
                try:
                    shutil.copy2(source, temp_name)
                    if self._file_digest(Path(temp_name)) != source_digest:
                        raise StatePathConflict(f"Copied session state failed digest verification: {source}")
                    os.replace(temp_name, target)
                finally:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)

            files[relative] = {"kind": kind, "source_digest": source_digest}
            self._write_manifest(manifest)


__all__ = ["AurenStatePaths", "StatePathConflict"]
