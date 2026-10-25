#!/usr/bin/env python3
"""Build, verify, install and update isolated AUREN distributions.

AUREN is machine-scoped. Project repositories are workspaces only and are never
used as an installation location. Installed builds are immutable and pinned by
semantic version, exact source commit and bundle hash, so updates are auditable
and rollbacks are deterministic.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import signal
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from threading import Event

from .guarded_network import guarded_https_get

BUNDLE_FORMAT_VERSION = 2
BUNDLE_NAME = "auren-portable"
AUREN_REPOSITORY = "sourabhJainR/AUREN"
AUREN_BRANCH = "main"
MANIFEST_NAME = "auren-bundle.json"
PAYLOAD_ROOT = "payload"
LAUNCHER_PATH = "auren_cli.py"
REQUIRED_PATHS = (".ai-harness", ".claude-plugin", "skills/ai-coding-orchestrator", "portable", "dashboard")
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git", "worktrees"}
MUTABLE_FILE_NAMES = {"execution.journal.jsonl", "telemetry.jsonl", "task-memory.jsonl", "task-memory.jsonl.lock", "learning-journal.jsonl", "evidence-ledger.jsonl", "regression-events.jsonl"}
MAX_ARCHIVE_MEMBERS = 20000
MAX_MEMBER_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000

MUTABLE_STATE_SUFFIXES = (
    ".sqlite", ".sqlite3", ".db", ".db3",
    ".sqlite-wal", ".sqlite-shm", ".sqlite-journal",
    ".sqlite3-wal", ".sqlite3-shm", ".sqlite3-journal",
    ".db-wal", ".db-shm", ".db-journal",
    ".db3-wal", ".db3-shm", ".db3-journal",
)

@dataclass(frozen=True)
class FileRecord:
    path: str
    sha256: str
    size: int

def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]

def home_auren(auren_home: Path | None = None) -> Path:
    return (auren_home or (Path.home() / ".auren")).expanduser().resolve()

def _progress_snapshot_rows(project_root: Path, stale_after_seconds: int) -> tuple[list[dict], bool]:
    """Read durable progress from the workspace or canonical supervisor state."""
    from .identity_compat import canonical_auren_home
    from .progress_journal import ProgressJournal

    candidates = (
        project_root.expanduser().resolve() / ".auren" / "progress.jsonl",
        canonical_auren_home() / "executive" / ".auren" / "progress.jsonl",
    )
    snapshots: dict[str, dict] = {}
    seen: set[str] = set()
    degraded = False
    for path in candidates:
        key = str(path.resolve())
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        try:
            rows = ProgressJournal(path).list_snapshots(stale_after_seconds=stale_after_seconds)
        except Exception:
            degraded = True
            rows = [{
                "task_id": "progress-journal", "state": "unknown", "reported_state": "unknown",
                "stage": "journal", "message": "Progress journal could not be read safely.",
                "last_update": None, "seconds_since_update": None, "stale": False,
                "progress_percent": None, "completed_units": None, "total_units": None,
                "event_count": 0, "next_action": "inspect journal integrity before trusting progress",
            }]
        for row in rows:
            snapshots[str(row.get("task_id", "unknown"))] = row
    ordered = sorted(
        snapshots.values(), key=lambda row: str(row.get("last_update") or ""), reverse=True
    )
    return ordered[:100], degraded


def _print_progress(args) -> int:
    if type(args.stale_after_seconds) is not int or not 1 <= args.stale_after_seconds <= 86400:
        raise SystemExit("--stale-after-seconds must be between 1 and 86400")
    rows, degraded = _progress_snapshot_rows(args.project_root, args.stale_after_seconds)
    if args.task_id:
        rows = [row for row in rows if row.get("task_id") == args.task_id]
    if args.task_id and not rows and not degraded:
        payload = {"status": "not_found", "task_id": args.task_id, "tasks": []}
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"No progress recorded for task {args.task_id}.")
        return 1
    status = "degraded" if degraded else ("ok" if rows else "empty")
    if args.json:
        print(json.dumps({"status": status, "tasks": rows}, indent=2, sort_keys=True))
    elif not rows:
        print("No durable task progress recorded.")
    else:
        for row in rows:
            age = row.get("seconds_since_update")
            age_text = f"{age}s since update" if age is not None else "age unknown"
            print(
                f"{row.get('state', 'unknown'):<10} {row.get('task_id', 'unknown')} "
                f"stage={row.get('stage') or 'unknown'} seq={row.get('sequence', '?')} "
                f"{age_text} | {row.get('message') or ''}"
            )
            if row.get("next_action"):
                print(f"  next: {row['next_action']}")
    return 2 if degraded else 0


def _is_mutable_state_path(path: str | Path) -> bool:
    """Use one mutable-state policy for bundle creation, verification and installation."""
    normalized = str(path).replace("\\", "/").lower()
    parts = PurePosixPath(normalized).parts
    for index in range(len(parts) - 1):
        if parts[index] == ".ai-harness" and parts[index + 1] in {"learning", "evidence"}:
            # This checked-in, immutable prior set is required by portable
            # compiler-bootstrap workflows. Other files in learning/evidence
            # are mutable state and remain excluded by default.
            if parts[index:] == (".ai-harness", "learning", "compiler_bootstrap_priors.jsonl"):
                return False
            return True
    name = PurePosixPath(normalized).name
    return name in MUTABLE_FILE_NAMES or name.endswith(MUTABLE_STATE_SUFFIXES)


def _reject_source_symlink(path: Path) -> None:
    if path.is_symlink():
        raise SystemExit(f"portable source contains a symlink; refusing ambiguous payload: {path}")


def should_include(path: Path, root: Path) -> bool:
    _reject_source_symlink(path)
    rel = path.relative_to(root)
    if any(part in EXCLUDED_PARTS for part in rel.parts):
        return False
    if not path.is_file() or _is_mutable_state_path(path):
        return False
    return True

def iter_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for top in REQUIRED_PATHS:
        base = root / top
        if not base.exists():
            raise SystemExit(f"required source path is missing: {base}")
        _reject_source_symlink(base)
        for path in base.rglob("*"):
            _reject_source_symlink(path)
            if path.is_file() and should_include(path, root):
                files.append(path)
    # The bootstrap-safe environment resolver is a runtime dependency outside
    # the portable package tree; pin it in the bundle manifest and payload.
    resolver = root / "auren_env_compat.py"
    _reject_source_symlink(resolver)
    if not resolver.is_file():
        raise SystemExit(f"required source file is missing: {resolver}")
    files.append(resolver)
    return sorted(set(files), key=lambda p: p.relative_to(root).as_posix())

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _plugin_version(root: Path) -> str:
    version_file = root / "VERSION"
    if version_file.is_file():
        version = version_file.read_text(encoding="utf-8").strip()
        if version:
            return version
    path = root / ".claude-plugin" / "plugin.json"
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        version = value.get("version")
        if isinstance(version, str) and version:
            return version
    config = root / ".ai-harness" / "config.toml"
    if config.exists():
        for line in config.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("version ="):
                raw = line.split("=", 1)[1].strip().strip('"\'')
                return f"{raw}.0.0" if raw.isdigit() else raw
    return "0.0.0"

def _version_tuple(version: str) -> tuple[int, int, int]:
    values = []
    for part in version.split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        values.append(int(digits or 0))
    while len(values) < 3:
        values.append(0)
    return tuple(values[:3])

def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and len(value) >= 7 else None

def make_manifest(root: Path, files: list[Path], source_commit: str | None = None, source_ref: str = AUREN_BRANCH) -> dict:
    records = [FileRecord(p.relative_to(root).as_posix(), sha256_file(p), p.stat().st_size).__dict__ for p in files]
    launcher = root / LAUNCHER_PATH
    _reject_source_symlink(launcher)
    if not launcher.is_file():
        raise SystemExit(f"required launcher is missing: {launcher}")
    records.append(FileRecord(LAUNCHER_PATH, sha256_file(launcher), launcher.stat().st_size).__dict__)
    records.sort(key=lambda item: item["path"])
    version = _plugin_version(root)
    return {
        "format": BUNDLE_FORMAT_VERSION,
        "name": BUNDLE_NAME,
        "version": version,
        "harness_version": _version_tuple(version)[0],
        "portable": True,
        "isolated_install": True,
        "provider_neutral": True,
        "target_repository_mutation": False,
        "source_repository": AUREN_REPOSITORY,
        "source_ref": source_ref,
        "source_commit": source_commit or _git_head(root),
        "mutable_state_excluded": True,
        "files": records,
    }

def build(root: Path, output: Path, source_commit: str | None = None, source_ref: str = AUREN_BRANCH) -> Path:
    files = iter_source_files(root)
    manifest = make_manifest(root, files, source_commit=source_commit, source_ref=source_ref)
    if not manifest["source_commit"]:
        raise SystemExit("bundle source commit could not be pinned; use --source-commit")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        for source in files:
            archive.write(source, f"{PAYLOAD_ROOT}/{source.relative_to(root).as_posix()}")
        archive.write(root / LAUNCHER_PATH, LAUNCHER_PATH)
        archive.writestr(f"{PAYLOAD_ROOT}/PORTABLE_BUNDLE.txt", "AUREN portable bundle\nInstallation is user-scoped and repository-isolated.\nVersion and exact source commit are pinned in the manifest.\n")
    return output

def _validate_archive_member_name(name: str) -> None:
    """Reject archive paths that can escape or become ambiguous on Windows."""
    if not name or "\x00" in name or "\\" in name:
        raise SystemExit(f"unsafe archive member path: {name!r}")
    relative = PurePosixPath(name)
    if (
        name in (".", "./")
        or relative.is_absolute()
        or relative.as_posix() != name
        or any(part in ("", ".", "..") for part in relative.parts)
    ):
        raise SystemExit(f"unsafe archive member path: {name!r}")
    # Drive-qualified and alternate-stream paths are not portable across hosts.
    if any(":" in part for part in relative.parts):
        raise SystemExit(f"non-portable archive member path: {name!r}")


def _validate_archive_limits(archive: zipfile.ZipFile) -> None:
    """Bound archive expansion before reading or extracting untrusted ZIP data."""
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise SystemExit(f"archive exceeds member limit ({len(members)} > {MAX_ARCHIVE_MEMBERS})")
    total_uncompressed = 0
    for member in members:
        if member.file_size < 0 or member.compress_size < 0:
            raise SystemExit(f"archive has invalid size metadata: {member.filename!r}")
        if member.file_size > MAX_MEMBER_UNCOMPRESSED_BYTES:
            raise SystemExit(f"archive member exceeds uncompressed size limit: {member.filename!r}")
        total_uncompressed += member.file_size
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise SystemExit("archive exceeds total uncompressed size limit")
        if member.file_size and member.compress_size == 0:
            raise SystemExit(f"archive member has invalid compression metadata: {member.filename!r}")
        if member.compress_size and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO:
            raise SystemExit(f"archive member exceeds compression ratio limit: {member.filename!r}")


def safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    _validate_archive_limits(archive)
    destination = destination.resolve()
    names = [member.filename for member in archive.infolist()]
    if len(names) != len(set(names)):
        raise SystemExit("archive contains duplicate members")
    for member in archive.infolist():
        _validate_archive_member_name(member.filename)
        target = (destination / member.filename).resolve()
        if destination != target and destination not in target.parents:
            raise SystemExit(f"unsafe archive path: {member.filename}")
    archive.extractall(destination)

def _manifest_record_path(root: Path, record_path: str) -> Path:
    _validate_archive_member_name(record_path)
    relative = PurePosixPath(record_path)
    if record_path == LAUNCHER_PATH:
        return root / LAUNCHER_PATH
    if record_path == "auren_env_compat.py":
        return root / PAYLOAD_ROOT / record_path
    if not any(record_path == prefix or record_path.startswith(f"{prefix}/") for prefix in REQUIRED_PATHS):
        raise SystemExit(f"unexpected manifest path: {record_path}")
    return root / PAYLOAD_ROOT / record_path

def _native_bundle(bundle: Path) -> tuple[Path, Path | None]:
    """Return a native AUREN bundle, transparently unwrapping upload-artifact ZIPs.

    GitHub Actions ``upload-artifact`` packages every uploaded file in another
    ZIP archive. Therefore downloading an artifact containing ``auren-portable.zip``
    produces ``artifact.zip -> auren-portable.zip``. The CLI accepts both forms so
    users can pass the downloaded artifact directly to ``install`` or ``verify``.
    """
    bundle = Path(bundle).expanduser().resolve()
    if not bundle.is_file():
        raise SystemExit(f"AUREN bundle does not exist: {bundle}")
    try:
        with zipfile.ZipFile(bundle) as archive:
            _validate_archive_limits(archive)
            all_names = [member.filename for member in archive.infolist()]
            if len(all_names) != len(set(all_names)):
                raise SystemExit("invalid artifact wrapper: duplicate archive members")
            names = [name for name in all_names if not name.endswith("/")]
            if MANIFEST_NAME in names:
                return bundle, None
            zip_members = [name for name in names if name.lower().endswith(".zip")]
            if len(names) == 1 and len(zip_members) == 1 and zip_members[0] == f"{BUNDLE_NAME}.zip":
                temp_root = Path(tempfile.mkdtemp(prefix="auren-artifact-"))
                inner = temp_root / f"{BUNDLE_NAME}.zip"
                inner.write_bytes(archive.read(zip_members[0]))
                try:
                    with zipfile.ZipFile(inner) as nested:
                        _validate_archive_limits(nested)
                        if MANIFEST_NAME not in [name for name in nested.namelist() if not name.endswith("/")]:
                            raise SystemExit("artifact contains auren-portable.zip, but it is not a valid AUREN bundle")
                except zipfile.BadZipFile as exc:
                    raise SystemExit(f"artifact contains an invalid auren-portable.zip: {exc}") from exc
                return inner, temp_root
    except zipfile.BadZipFile as exc:
        raise SystemExit(f"invalid AUREN bundle: {exc}") from exc
    raise SystemExit(
        "invalid AUREN bundle: expected auren-bundle.json at the archive root; "
        "if this is a GitHub Actions artifact, it must contain exactly auren-portable.zip"
    )

def _verify_native_bundle(bundle: Path) -> dict:
    try:
        with zipfile.ZipFile(bundle) as archive:
            _validate_archive_limits(archive)
            members = archive.infolist()
            member_names = [member.filename for member in members]
            if len(member_names) != len(set(member_names)):
                raise SystemExit("bundle contains duplicate archive members")
            if any(name.endswith("/") for name in member_names):
                raise SystemExit("bundle contains unexpected directory entries")
            if MANIFEST_NAME not in member_names:
                raise SystemExit("bundle manifest is missing")
            for name in member_names:
                _validate_archive_member_name(name)
            manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
            if manifest.get("format") != BUNDLE_FORMAT_VERSION:
                raise SystemExit("unsupported bundle format")
            if manifest.get("target_repository_mutation") is not False:
                raise SystemExit("bundle is not marked repository-isolated")
            if not manifest.get("version") or not manifest.get("source_commit"):
                raise SystemExit("bundle must carry a version and exact source commit pin")
            records = manifest.get("files")
            if not isinstance(records, list) or not records:
                raise SystemExit("bundle manifest file inventory is missing")
            expected_members = {MANIFEST_NAME, LAUNCHER_PATH, f"{PAYLOAD_ROOT}/PORTABLE_BUNDLE.txt"}
            seen: set[str] = set()
            for record in records:
                if (
                    not isinstance(record, dict)
                    or not isinstance(record.get("path"), str)
                    or not isinstance(record.get("sha256"), str)
                    or type(record.get("size")) is not int
                    or record["size"] < 0
                    or len(record["sha256"]) != 64
                    or any(ch not in "0123456789abcdef" for ch in record["sha256"].lower())
                ):
                    raise SystemExit("invalid bundle file record")
                record_path = record["path"]
                if record_path != PurePosixPath(record_path).as_posix() or "//" in record_path or record_path.endswith("/"):
                    raise SystemExit(f"non-canonical manifest path: {record_path}")
                if record_path in seen:
                    raise SystemExit(f"duplicate bundle file record: {record_path}")
                seen.add(record_path)
                if _is_mutable_state_path(record_path):
                    raise SystemExit(f"mutable runtime state is forbidden in bundle: {record_path}")
                if record["size"] < 0:
                    raise SystemExit(f"invalid bundle file size: {record_path}")
                # Validate the logical path before using it to derive the archive inventory.
                _manifest_record_path(Path("."), record_path)
                if record_path != LAUNCHER_PATH:
                    expected_members.add(f"{PAYLOAD_ROOT}/{record_path}")
            if LAUNCHER_PATH not in seen:
                raise SystemExit(f"bundle launcher is not covered by manifest: {LAUNCHER_PATH}")
            actual_members = set(member_names)
            missing = sorted(expected_members - actual_members)
            unexpected = sorted(actual_members - expected_members)
            if missing or unexpected:
                raise SystemExit(
                    "bundle archive inventory does not match manifest "
                    f"(missing={missing}, unexpected={unexpected})"
                )
            root = Path(tempfile.mkdtemp(prefix="auren-verify-"))
            try:
                safe_extract(archive, root)
                for record in records:
                    record_path = record["path"]
                    path = _manifest_record_path(root, record_path)
                    if not path.is_file() or sha256_file(path) != record["sha256"]:
                        raise SystemExit(f"bundle integrity failure: {record_path}")
                    if "size" in record and record["size"] != path.stat().st_size:
                        raise SystemExit(f"bundle size mismatch: {record_path}")
                if not (root / LAUNCHER_PATH).is_file():
                    raise SystemExit("bundle launcher is missing: auren_cli.py")
                return manifest
            finally:
                shutil.rmtree(root, ignore_errors=True)
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid AUREN bundle: {exc}") from exc

def verify_bundle(bundle: Path) -> dict:
    native, temp_root = _native_bundle(bundle)
    try:
        return _verify_native_bundle(native)
    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)

def _copy_tree_without_mutable_state(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        rel = item.relative_to(source)
        if any(part in EXCLUDED_PARTS for part in rel.parts) or _is_mutable_state_path(rel):
            continue
        target = destination / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)

def _copy_payload(payload: Path, version_root: Path) -> None:
    version_root.mkdir(parents=True, exist_ok=True)
    for name in (".ai-harness", ".claude-plugin", "portable", "dashboard"):
        source = payload / name
        if source.is_dir():
            _copy_tree_without_mutable_state(source, version_root / name)
    skill = payload / "skills" / "ai-coding-orchestrator"
    if skill.is_dir():
        _copy_tree_without_mutable_state(skill, version_root / "skills" / "ai-coding-orchestrator")
    resolver = payload / "auren_env_compat.py"
    if not resolver.is_file():
        raise SystemExit("bundle environment compatibility resolver is missing")
    shutil.copy2(resolver, version_root / resolver.name)
    launcher = payload.parent / LAUNCHER_PATH
    if not launcher.is_file():
        raise SystemExit("bundle launcher is missing: auren_cli.py")
    shutil.copy2(launcher, version_root / LAUNCHER_PATH)

def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def _append_jsonl_atomic(path: Path, value: dict) -> None:
    """Append one durable JSONL event by replacing a fully written sibling file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    previous = path.read_bytes() if path.is_file() else b""
    with temp.open("wb") as handle:
        handle.write(previous)
        handle.write((json.dumps(value, sort_keys=True) + "\n").encode("utf-8"))
        handle.flush()
    temp.replace(path)

def _remove_path(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _set_current(root: Path, version_root: Path) -> None:
    """Switch the active build without deleting the last working selection first."""
    root.mkdir(parents=True, exist_ok=True)
    current = root / "current"
    staged = root / ".current.tmp"
    backup = root / ".current.backup"
    _remove_path(staged)
    # A previous process may have stopped after moving current to backup.
    if backup.exists() or backup.is_symlink():
        if not (current.exists() or current.is_symlink()):
            backup.replace(current)
        else:
            _remove_path(backup)
    try:
        staged.symlink_to(version_root, target_is_directory=True)
        # Replacing a symlink is atomic on supported filesystems.
        staged.replace(current)
        return
    except OSError:
        _remove_path(staged)

    # Windows and restricted filesystems may not support directory symlinks.
    # Copy to a sibling first, then rename the previous selection out of the way.
    temp_dir = root / ".current-copy.tmp"
    _remove_path(temp_dir)
    _copy_tree_without_mutable_state(version_root, temp_dir)
    had_current = current.exists() or current.is_symlink()
    try:
        if had_current:
            current.replace(backup)
        temp_dir.replace(current)
    except OSError:
        backup_exists = backup.exists() or backup.is_symlink()
        if backup_exists:
            _remove_path(current)
            backup.replace(current)
        elif not had_current:
            _remove_path(current)
        # If the old selection was never moved to backup, leave it untouched.
        raise
    else:
        _remove_path(backup)
    finally:
        _remove_path(temp_dir)

def _skill_destinations() -> dict[str, Path]:
    return {
        "agents": Path.home() / ".agents" / "skills" / "ai-coding-orchestrator",
        "claude": Path.home() / ".claude" / "skills" / "ai-coding-orchestrator",
        "gemini": Path.home() / ".gemini" / "skills" / "ai-coding-orchestrator",
    }

def _sync_skills(source: Path, mode: str) -> None:
    if mode == "none":
        return
    destinations = _skill_destinations()
    selected = list(destinations) if mode == "all" else ([name for name, path in destinations.items() if path.exists()] if mode == "auto" else [mode])
    for name in selected:
        _install_skill(source, destinations[name])

def _install_skill(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise SystemExit("bundle does not contain the canonical Agent Skill")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_name(destination.name + ".staging")
    backup = destination.with_name(destination.name + ".backup")
    _remove_path(staged)
    # Recover a prior skill if a process stopped during the replacement window.
    if backup.exists() or backup.is_symlink():
        if not (destination.exists() or destination.is_symlink()):
            backup.replace(destination)
        else:
            _remove_path(backup)
    had_destination = destination.exists() or destination.is_symlink()
    try:
        shutil.copytree(source, staged)
        if had_destination:
            destination.replace(backup)
        staged.replace(destination)
    except OSError:
        backup_exists = backup.exists() or backup.is_symlink()
        if backup_exists:
            _remove_path(destination)
            backup.replace(destination)
        elif not had_destination:
            _remove_path(destination)
        # A copy failure before the rename must not remove the original skill.
        raise
    else:
        _remove_path(backup)
    finally:
        _remove_path(staged)

def _version_install_root(root: Path, version: str, bundle_hash: str) -> Path:
    """Return an immutable build-specific installation directory.

    Semantic version alone is not a unique build identity. CI can legitimately
    produce multiple builds with version 20.1.1 while the source commit or
    bundle contents differ. Keying the directory by the verified bundle hash
    keeps every build immutable and lets ``current`` switch between builds.
    """
    return root / "versions" / f"v{version}-{bundle_hash[:12]}"

def install(bundle: Path, install_skill: str = "agents", auren_home: Path | None = None) -> Path:
    native_bundle, temp_root = _native_bundle(bundle)
    try:
        manifest = _verify_native_bundle(native_bundle)
        root = home_auren(auren_home)
        version = str(manifest["version"])
        bundle_hash = sha256_file(native_bundle)
        version_root = _version_install_root(root, version, bundle_hash)
        versions_root = version_root.parent
        versions_root.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix="auren-install-"))
        staged_version: Path | None = None
        try:
            with zipfile.ZipFile(native_bundle) as archive:
                safe_extract(archive, temp)

            existing = version_root / "install.json"
            if existing.is_file():
                prior = json.loads(existing.read_text(encoding="utf-8"))
                if prior.get("source_commit") != manifest["source_commit"] or prior.get("bundle_sha256") != bundle_hash:
                    raise SystemExit(f"build directory {version_root.name} is already pinned to different content; refusing overwrite")
                if not (version_root / LAUNCHER_PATH).is_file():
                    raise SystemExit(f"pinned build is incomplete: {version_root}")
            elif version_root.exists():
                raise SystemExit(f"version directory exists without a pin: {version_root}")
            else:
                staged_version = Path(tempfile.mkdtemp(prefix=f".{version_root.name}.staging-", dir=versions_root))
                _copy_payload(temp / PAYLOAD_ROOT, staged_version)
                record = {
                    "format": BUNDLE_FORMAT_VERSION,
                    "version": version,
                    "harness_version": manifest.get("harness_version"),
                    "source_repository": manifest.get("source_repository"),
                    "source_ref": manifest.get("source_ref"),
                    "source_commit": manifest.get("source_commit"),
                    "bundle_sha256": bundle_hash,
                    "install_root": version_root.name,
                    "installed_at": datetime.now(timezone.utc).isoformat(),
                    "repository_isolated": True,
                }
                _atomic_json(staged_version / "install.json", record)
                # A complete pinned build appears at its final path in one rename.
                staged_version.replace(version_root)
                staged_version = None

            record = json.loads((version_root / "install.json").read_text(encoding="utf-8"))
            previous_active_path = root / "active.json"
            previous_active = json.loads(previous_active_path.read_text(encoding="utf-8")) if previous_active_path.is_file() else None
            current = root / "current"
            previous_current_target: Path | None = None
            if current.is_symlink():
                previous_current_target = current.resolve()
            elif current.is_dir() and (current / "install.json").is_file():
                current_record = json.loads((current / "install.json").read_text(encoding="utf-8"))
                previous_name = current_record.get("install_root")
                if isinstance(previous_name, str) and Path(previous_name).name == previous_name:
                    previous_current_target = versions_root / previous_name

            active_written = False
            skills_sync_attempted = False
            try:
                _set_current(root, version_root)
                _atomic_json(previous_active_path, record)
                active_written = True
                skills_sync_attempted = install_skill != "none"
                _sync_skills(temp / PAYLOAD_ROOT / "skills" / "ai-coding-orchestrator", install_skill)
                _append_jsonl_atomic(root / "history.jsonl", {"event": "activate", **record})
            except Exception:
                # Restore the last known active build, metadata and selected skill
                # if any activation step fails. Preserve the original exception.
                if previous_current_target is not None and previous_current_target.is_dir():
                    _set_current(root, previous_current_target)
                elif current.exists() or current.is_symlink():
                    _remove_path(current)
                if active_written:
                    if previous_active is None:
                        _remove_path(previous_active_path)
                    else:
                        _atomic_json(previous_active_path, previous_active)
                if skills_sync_attempted and previous_active is not None:
                    previous_name = previous_active.get("install_root")
                    if isinstance(previous_name, str) and Path(previous_name).name == previous_name:
                        previous_skill = versions_root / previous_name / "skills" / "ai-coding-orchestrator"
                        if previous_skill.is_dir():
                            try:
                                _sync_skills(previous_skill, install_skill)
                            except Exception:
                                pass
                raise

            print(f"Installed and pinned AUREN {version} ({manifest['source_commit'][:12]})")
            print(f"Build: {version_root.name}")
            print(f"Active installation: {root / 'current'}")
            print("Repository isolation: ON")
            return root / "current"
        finally:
            if staged_version is not None:
                shutil.rmtree(staged_version, ignore_errors=True)
            shutil.rmtree(temp, ignore_errors=True)
    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)

def _http_json(url: str) -> dict:
    status, raw = guarded_https_get(
        url,
        allowed_hosts=("api.github.com", "raw.githubusercontent.com"),
        headers={"Accept": "application/vnd.github+json", "User-Agent": "auren-portable"},
        timeout=20,
        max_bytes=2 * 1024 * 1024,
    )
    if status >= 400:
        raise SystemExit("remote update metadata request failed")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise SystemExit("unexpected update metadata")
    return value

def _remote_target(ref: str) -> tuple[str, str]:
    commit_data = _http_json(f"https://api.github.com/repos/{AUREN_REPOSITORY}/commits/{ref}")
    commit = commit_data.get("sha")
    if not isinstance(commit, str) or not commit:
        raise SystemExit("remote AUREN commit could not be resolved")
    plugin = _http_json(f"https://raw.githubusercontent.com/{AUREN_REPOSITORY}/{commit}/.claude-plugin/plugin.json")
    version = plugin.get("version")
    if not isinstance(version, str) or not version:
        raise SystemExit("remote AUREN version could not be resolved")
    return version, commit

def _download_source(ref: str, destination: Path) -> Path:
    request = urllib.request.Request(f"https://api.github.com/repos/{AUREN_REPOSITORY}/zipball/{ref}", headers={"User-Agent": "auren-portable"})
    archive_path = destination / "source.zip"
    try:
        with urllib.request.urlopen(request, timeout=60) as response, archive_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except urllib.error.URLError as exc:
        raise SystemExit(f"unable to download AUREN update source: {exc}") from exc
    root = destination / "source"
    root.mkdir()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            safe_extract(archive, root)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"downloaded AUREN source is invalid: {exc}") from exc
    candidates = [p for p in root.iterdir() if p.is_dir()]
    if len(candidates) != 1:
        raise SystemExit("unexpected AUREN source archive layout")
    return candidates[0]

def check_update(auren_home: Path | None = None, ref: str = AUREN_BRANCH) -> dict:
    root = home_auren(auren_home)
    active = json.loads((root / "active.json").read_text(encoding="utf-8")) if (root / "active.json").is_file() else {}
    remote_version, remote_commit = _remote_target(ref)
    current_version = str(active.get("version") or "0.0.0")
    current_commit = str(active.get("source_commit") or "")
    update_available = (
        _version_tuple(remote_version) > _version_tuple(current_version)
        or (
            _version_tuple(remote_version) == _version_tuple(current_version)
            and bool(remote_commit)
            and remote_commit != current_commit
        )
    )
    return {
        "current_version": active.get("version"),
        "current_commit": active.get("source_commit"),
        "latest_version": remote_version,
        "latest_commit": remote_commit,
        "update_available": update_available,
    }

def _history_records(root: Path) -> list[dict]:
    history = root / "history.jsonl"
    if not history.is_file():
        return []
    records: list[dict] = []
    for line in history.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("event") in {"activate", "rollback"}:
            records.append(value)
    return records

def rollback(auren_home: Path | None = None) -> Path:
    root = home_auren(auren_home)
    active_path = root / "active.json"
    active = json.loads(active_path.read_text(encoding="utf-8")) if active_path.is_file() else {}
    active_key = (active.get("source_commit"), active.get("bundle_sha256"))
    target: dict | None = None
    for record in reversed(_history_records(root)):
        key = (record.get("source_commit"), record.get("bundle_sha256"))
        if key != active_key:
            target = record
            break
    if target is None:
        raise SystemExit("no previous immutable AUREN version is available")
    install_root = str(target.get("install_root") or f"v{target.get('version')}")
    if Path(install_root).name != install_root or install_root in {".", ".."}:
        raise SystemExit("rollback target contains an unsafe installation path")
    versions_root = root / "versions"
    version_root = versions_root / install_root
    if not (version_root / "install.json").is_file() or not (version_root / LAUNCHER_PATH).is_file():
        raise SystemExit(f"rollback target is incomplete: {version_root}")

    current = root / "current"
    previous_current_target: Path | None = None
    if current.is_symlink():
        previous_current_target = current.resolve()
    elif current.is_dir() and (current / "install.json").is_file():
        current_record = json.loads((current / "install.json").read_text(encoding="utf-8"))
        previous_name = current_record.get("install_root")
        if isinstance(previous_name, str) and Path(previous_name).name == previous_name:
            previous_current_target = versions_root / previous_name

    active_written = False
    skills_sync_attempted = False
    try:
        _set_current(root, version_root)
        _atomic_json(active_path, target)
        active_written = True
        skills_sync_attempted = True
        _sync_skills(version_root / "skills" / "ai-coding-orchestrator", "agents")
        _append_jsonl_atomic(root / "history.jsonl", {"event": "rollback", **target})
    except Exception:
        if previous_current_target is not None and previous_current_target.is_dir():
            _set_current(root, previous_current_target)
        elif current.exists() or current.is_symlink():
            _remove_path(current)
        if active_written:
            _atomic_json(active_path, active)
        if skills_sync_attempted:
            previous_name = active.get("install_root")
            if isinstance(previous_name, str) and Path(previous_name).name == previous_name:
                previous_skill = versions_root / previous_name / "skills" / "ai-coding-orchestrator"
                if previous_skill.is_dir():
                    try:
                        _sync_skills(previous_skill, "agents")
                    except Exception:
                        pass
        raise
    print(f"Rolled back AUREN to {target['version']} ({str(target.get('source_commit', ''))[:12]})")
    return root / "current"

def update(auren_home: Path | None = None, ref: str = AUREN_BRANCH, install_skill: str = "agents") -> Path:
    remote_version, remote_commit = _remote_target(ref)
    root = home_auren(auren_home)
    active = json.loads((root / "active.json").read_text(encoding="utf-8")) if (root / "active.json").is_file() else {}
    current_version = str(active.get("version") or "0.0.0")
    current_commit = str(active.get("source_commit") or "")
    comparison = (_version_tuple(remote_version), remote_commit)
    current_comparison = (_version_tuple(current_version), current_commit)
    if remote_version == current_version and remote_commit == current_commit:
        raise SystemExit("AUREN is already up to date")
    if _version_tuple(remote_version) < _version_tuple(current_version):
        raise SystemExit(f"refusing downgrade from {current_version} to {remote_version}; use rollback for a pinned previous version")
    if comparison == current_comparison:
        raise SystemExit("AUREN is already up to date")
    with tempfile.TemporaryDirectory(prefix="auren-update-") as tmp:
        source = _download_source(remote_commit, Path(tmp))
        bundle = Path(tmp) / "auren-portable.zip"
        build(source, bundle, source_commit=remote_commit, source_ref=ref)
        install(bundle, install_skill, root)
    return root / "current"

def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="AUREN portable distribution manager")
    sub = root.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--output", type=Path, default=Path("auren-portable.zip"))
    build_parser.add_argument("--source-commit", default=None)
    build_parser.add_argument("--source-ref", default=AUREN_BRANCH)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("bundle", type=Path)
    install_parser = sub.add_parser("install")
    install_parser.add_argument("bundle", type=Path)
    install_parser.add_argument("--skill", choices=("none", "agents", "claude", "gemini", "all", "auto"), default="agents")
    install_parser.add_argument("--auren-home", type=Path, default=None)
    update_parser = sub.add_parser("update")
    update_parser.add_argument("--ref", default=AUREN_BRANCH)
    update_parser.add_argument("--skill", choices=("none", "agents", "claude", "gemini", "all", "auto"), default="agents")
    update_parser.add_argument("--auren-home", type=Path, default=None)
    check_parser = sub.add_parser("check-update")
    check_parser.add_argument("--ref", default=AUREN_BRANCH)
    check_parser.add_argument("--auren-home", type=Path, default=None)
    rollback_parser = sub.add_parser("rollback")
    rollback_parser.add_argument("--auren-home", type=Path, default=None)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--json", action="store_true")
    status_parser.add_argument("--auren-home", type=Path, default=None)
    progress_parser = sub.add_parser("progress", help="Show truthful durable task progress.")
    progress_parser.add_argument("--project-root", type=Path, default=Path("."))
    progress_parser.add_argument("--task-id", default=None)
    progress_parser.add_argument("--stale-after-seconds", type=int, default=60)
    progress_parser.add_argument("--json", action="store_true")
    console_parser = sub.add_parser("console")
    console_parser.add_argument("--host", default="127.0.0.1")
    console_parser.add_argument("--port", type=int, default=0)
    console_parser.add_argument("--open", dest="open_browser", action="store_true")
    console_parser.add_argument("--auren-home", type=Path, default=None)
    worker_parser = sub.add_parser("worker")
    worker_parser.add_argument("--project-root", type=Path, default=Path("."))
    worker_parser.add_argument("--work-key", default=None, help="Stable durable work key; requires --goal")
    worker_parser.add_argument("--goal", default=None, help="Goal to bind to --work-key before draining durable tasks")
    worker_parser.add_argument("--batch-size", type=int, default=20)
    worker_parser.add_argument("--idle-sleep-seconds", type=float, default=1.0)
    worker_parser.add_argument("--max-cycles", type=int, default=100)
    worker_parser.add_argument("--max-idle-cycles", type=int, default=5)
    worker_parser.add_argument("--json", action="store_true")
    workbench_parser = sub.add_parser("workbench")
    workbench_parser.add_argument("--project-root", type=Path, default=Path("."))
    workbench_parser.add_argument("--workers", type=int, default=None)
    workbench_parser.add_argument("--mission", default="local-cli")
    workbench_parser.add_argument("--timeout", type=float, default=300.0)
    workbench_parser.add_argument("--effect", choices=("read_only", "mutating"), default="read_only")
    workbench_parser.add_argument("argv", nargs=argparse.REMAINDER)
    return root

def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "build":
        path = build(repo_root(), args.output, args.source_commit, args.source_ref)
        print(path)
    elif args.command == "verify":
        print(json.dumps(verify_bundle(args.bundle), indent=2, sort_keys=True))
    elif args.command == "install":
        install(args.bundle, args.skill, args.auren_home)
    elif args.command == "check-update":
        print(json.dumps(check_update(args.auren_home, args.ref), indent=2, sort_keys=True))
    elif args.command == "update":
        update(args.auren_home, args.ref, args.skill)
    elif args.command == "rollback":
        rollback(args.auren_home)
    elif args.command == "status":
        from .auren_console import collect_snapshot, render_status
        print(render_status(collect_snapshot(args.auren_home), args.json))
    elif args.command == "progress":
        return _print_progress(args)
    elif args.command == "console":
        from .auren_console import serve_console
        if args.host not in {"127.0.0.1", "localhost", "::1"}:
            raise SystemExit("console host must be loopback")
        serve_console(args.auren_home, args.host, args.port, args.open_browser, True)
    elif args.command == "worker":
        from .execution_controller import ExecutionController

        stop_event = Event()
        controller = ExecutionController(args.project_root)
        if bool(args.work_key) != bool(args.goal):
            raise SystemExit("worker --work-key and --goal must be supplied together")
        stable_work_unit_id = None
        if args.work_key:
            stable_work_unit_id = controller.work_unit_for_session(
                args.work_key, args.goal, work_key=args.work_key
            )

        def request_stop(_signum, _frame):
            stop_event.set()

        previous_handlers = {}
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, request_stop)
        try:
            report = controller.run_continuously(
                stop_event=stop_event,
                batch_size=args.batch_size,
                idle_sleep_seconds=args.idle_sleep_seconds,
                max_cycles=args.max_cycles,
                max_idle_cycles=args.max_idle_cycles,
            )
        finally:
            for signum, previous in previous_handlers.items():
                signal.signal(signum, previous)
        payload = {
            "stable_work_key": args.work_key,
            "stable_work_unit_id": stable_work_unit_id,
            "cycles": report.cycles,
            "processed_work_units": report.processed_work_units,
            "completed_tasks": report.completed_tasks,
            "failed_tasks": report.failed_tasks,
            "remaining_nonterminal_tasks": report.remaining_nonterminal_tasks,
            "task_state_counts": dict(report.task_state_counts),
            "idle_cycles": report.idle_cycles,
            "stop_reason": report.stop_reason,
            "trust_scores": [score.as_dict() for score in report.trust_scores],
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(
                "AUREN worker stopped: "
                f"{report.stop_reason}; cycles={report.cycles}; "
                f"processed_work_units={report.processed_work_units}; "
                f"completed_tasks={report.completed_tasks}; failed_tasks={report.failed_tasks}; "
                f"remaining_nonterminal_tasks={report.remaining_nonterminal_tasks}; "
                f"idle_cycles={report.idle_cycles}; "
                f"task_state_counts={json.dumps(payload['task_state_counts'], sort_keys=True)}"
            )
        if report.stop_reason == "cancelled":
            return 130
        if report.failed_tasks:
            return 1
        if report.remaining_nonterminal_tasks:
            return 2
        return 0
    elif args.command == "workbench":
        from .local_workbench import LocalWorkbench, packet
        argv = list(args.argv)
        if argv and argv[0] == "--":
            argv = argv[1:]
        if not argv:
            raise SystemExit("workbench requires a command after --")
        kwargs = {"max_workers": args.workers} if args.workers is not None else {}
        work = packet("local command", argv, mission_id=args.mission, effect=args.effect, timeout_seconds=args.timeout)
        receipt = LocalWorkbench(args.project_root, **kwargs).run([work])
        print(json.dumps(receipt.as_dict(), indent=2, sort_keys=True))
        return 0 if receipt.status == "completed" else 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
