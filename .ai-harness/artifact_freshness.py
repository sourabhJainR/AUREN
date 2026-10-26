"""Fail-closed freshness checks for optional repository graph artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_SUFFIX = ".auren-manifest.json"
MANIFEST_SCHEMA = 1
MAX_FILES = 20000
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


def _sha256_file(path: Path, limit: int) -> tuple[str | None, str | None]:
    digest = hashlib.sha256()
    total = 0
    try:
        before = path.stat()
        if before.st_size > limit:
            return None, "artifact_size_limit_exceeded"
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    return None, "artifact_size_limit_exceeded"
                digest.update(chunk)
        after = path.stat()
    except OSError:
        return None, "artifact_unreadable"
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return None, "artifact_changed_during_hash"
    return digest.hexdigest(), None


def repository_snapshot(root: str | Path) -> dict[str, Any]:
    """Hash file names and contents from a bounded Git working-tree inventory."""
    base = Path(root).expanduser().resolve()
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=base, capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "unavailable", "fresh": False, "reason": "repository_inventory_failed"}
    if result.returncode != 0:
        return {"status": "unavailable", "fresh": False, "reason": "repository_inventory_failed"}
    names = sorted(set(result.stdout.splitlines()))
    names = [
        name for name in names
        if name and not name.endswith(MANIFEST_SUFFIX)
    ]
    if len(names) > MAX_FILES:
        return {"status": "incomplete", "fresh": False, "reason": "file_count_limit_exceeded"}
    digest = hashlib.sha256()
    total_bytes = 0
    for name in names:
        rel = PurePosixPath(name)
        if rel.is_absolute() or ".." in rel.parts:
            return {"status": "incomplete", "fresh": False, "reason": "unsafe_repository_path"}
        path = base.joinpath(*rel.parts)
        try:
            info = path.lstat()
        except OSError:
            return {"status": "incomplete", "fresh": False, "reason": "file_disappeared_during_snapshot"}
        digest.update(name.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        if path.is_symlink():
            target = os.readlink(path).encode("utf-8", errors="surrogateescape")
            payload_digest = hashlib.sha256(b"symlink\0" + target).hexdigest()
            total_bytes += len(target)
        elif path.is_file():
            file_digest = hashlib.sha256()
            read_bytes = 0
            try:
                with path.open("rb") as stream:
                    while True:
                        chunk = stream.read(65536)
                        if not chunk:
                            break
                        read_bytes += len(chunk)
                        total_bytes += len(chunk)
                        if total_bytes > MAX_TOTAL_BYTES:
                            return {"status": "incomplete", "fresh": False, "reason": "snapshot_byte_limit_exceeded"}
                        file_digest.update(chunk)
                after = path.stat()
            except OSError:
                return {"status": "incomplete", "fresh": False, "reason": "file_unreadable_during_snapshot"}
            if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                return {"status": "incomplete", "fresh": False, "reason": "file_changed_during_snapshot"}
            payload_digest = file_digest.hexdigest()
        else:
            payload_digest = hashlib.sha256(f"non-regular:{info.st_mode}".encode()).hexdigest()
        digest.update(payload_digest.encode("ascii"))
        digest.update(b"\n")
    return {
        "status": "complete",
        "fresh": True,
        "snapshot_sha256": digest.hexdigest(),
        "file_count": len(names),
        "bytes_hashed": total_bytes,
    }


def build_artifact_manifest(artifact_path: str | Path, repository_root: str | Path) -> dict[str, Any]:
    """Create manifest data for a producer to persist beside a completed artifact."""
    artifact = Path(artifact_path).expanduser().resolve()
    root = Path(repository_root).expanduser().resolve()
    try:
        artifact.relative_to(root)
    except ValueError as exc:
        raise ValueError("artifact must be inside the repository root") from exc
    artifact_digest, error = _sha256_file(artifact, MAX_ARTIFACT_BYTES)
    if error or not artifact_digest:
        raise ValueError(error or "artifact_hash_failed")
    snapshot = repository_snapshot(root)
    if snapshot.get("status") != "complete":
        raise ValueError(str(snapshot.get("reason", "repository_snapshot_incomplete")))
    return {
        "schema_version": MANIFEST_SCHEMA,
        "artifact_sha256": artifact_digest,
        "repository_snapshot_sha256": snapshot["snapshot_sha256"],
        "file_count": snapshot["file_count"],
        "bytes_hashed": snapshot["bytes_hashed"],
    }


def validate_artifact_freshness(
    artifact_path: str | Path,
    repository_root: str | Path,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Freshness is true only when both artifact and repository digests match exactly."""
    artifact = Path(artifact_path).expanduser().resolve()
    root = Path(repository_root).expanduser().resolve()
    manifest = (
        Path(manifest_path).expanduser().resolve()
        if manifest_path is not None
        else artifact.with_name(artifact.name + MANIFEST_SUFFIX)
    )
    try:
        artifact.relative_to(root)
        manifest.relative_to(root)
    except ValueError:
        return {"status": "invalid", "fresh": False, "reason": "artifact_or_manifest_outside_repository"}
    if not artifact.is_file():
        return {"status": "unavailable", "fresh": False, "reason": "artifact_missing"}
    if not manifest.is_file():
        return {"status": "unverified", "fresh": False, "reason": "freshness_manifest_missing"}
    try:
        if manifest.stat().st_size > 1024 * 1024:
            raise ValueError("manifest_size_limit_exceeded")
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return {"status": "invalid", "fresh": False, "reason": "freshness_manifest_invalid"}
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data.get("schema_version") != MANIFEST_SCHEMA
        or not isinstance(data.get("artifact_sha256"), str)
        or len(data.get("artifact_sha256", "")) != 64
        or any(ch not in "0123456789abcdef" for ch in data.get("artifact_sha256", ""))
        or not isinstance(data.get("repository_snapshot_sha256"), str)
        or len(data.get("repository_snapshot_sha256", "")) != 64
        or any(ch not in "0123456789abcdef" for ch in data.get("repository_snapshot_sha256", ""))
    ):
        return {"status": "invalid", "fresh": False, "reason": "freshness_manifest_invalid"}
    artifact_digest, error = _sha256_file(artifact, MAX_ARTIFACT_BYTES)
    if error or artifact_digest != data["artifact_sha256"]:
        return {"status": "stale", "fresh": False, "reason": error or "artifact_digest_mismatch"}
    snapshot = repository_snapshot(root)
    if snapshot.get("status") != "complete":
        return {"status": snapshot.get("status", "unavailable"), "fresh": False, "reason": snapshot.get("reason", "repository_snapshot_incomplete")}
    if snapshot["snapshot_sha256"] != data["repository_snapshot_sha256"]:
        return {"status": "stale", "fresh": False, "reason": "repository_snapshot_digest_mismatch"}
    return {
        "status": "fresh",
        "fresh": True,
        "reason": "artifact_and_repository_digests_match",
        "artifact_sha256": artifact_digest,
        "repository_snapshot_sha256": snapshot["snapshot_sha256"],
    }
