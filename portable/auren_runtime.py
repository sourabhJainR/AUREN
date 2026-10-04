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
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

BUNDLE_FORMAT_VERSION = 2
BUNDLE_NAME = "auren-portable"
AUREN_REPOSITORY = "sourabhJainR/Hello_world_With_Structure"
AUREN_BRANCH = "main"
MANIFEST_NAME = "auren-bundle.json"
PAYLOAD_ROOT = "payload"
LAUNCHER_PATH = "auren_cli.py"
REQUIRED_PATHS = (".ai-harness", "skills/ai-coding-orchestrator", "portable", "dashboard")
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git", "worktrees"}
MUTABLE_FILE_NAMES = {"execution.journal.jsonl", "telemetry.jsonl", "task-memory.jsonl", "regression-events.jsonl"}

@dataclass(frozen=True)
class FileRecord:
    path: str
    sha256: str
    size: int

def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]

def home_auren(auren_home: Path | None = None) -> Path:
    return (auren_home or (Path.home() / ".auren")).expanduser().resolve()

def should_include(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if any(part in EXCLUDED_PARTS for part in rel.parts):
        return False
    return not (path.is_file() and path.name in MUTABLE_FILE_NAMES)

def iter_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for top in REQUIRED_PATHS:
        base = root / top
        if not base.exists():
            raise SystemExit(f"required source path is missing: {base}")
        files.extend(p for p in base.rglob("*") if p.is_file() and should_include(p, root))
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

def safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if destination != target and destination not in target.parents:
            raise SystemExit(f"unsafe archive path: {member.filename}")
    archive.extractall(destination)

def _manifest_record_path(root: Path, record_path: str) -> Path:
    relative = PurePosixPath(record_path)
    if relative.is_absolute() or ".." in relative.parts or not record_path or "\\" in record_path:
        raise SystemExit(f"unsafe manifest path: {record_path}")
    if record_path == LAUNCHER_PATH:
        return root / LAUNCHER_PATH
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
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if MANIFEST_NAME in names:
                return bundle, None
            zip_members = [name for name in names if name.lower().endswith(".zip")]
            if len(zip_members) == 1 and Path(zip_members[0]).name.lower() == f"{BUNDLE_NAME}.zip":
                temp_root = Path(tempfile.mkdtemp(prefix="auren-artifact-"))
                inner = temp_root / f"{BUNDLE_NAME}.zip"
                inner.write_bytes(archive.read(zip_members[0]))
                try:
                    with zipfile.ZipFile(inner) as nested:
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
            if MANIFEST_NAME not in archive.namelist():
                raise SystemExit("bundle manifest is missing")
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
            root = Path(tempfile.mkdtemp(prefix="auren-verify-"))
            try:
                safe_extract(archive, root)
                seen: set[str] = set()
                for record in records:
                    if not isinstance(record, dict) or not isinstance(record.get("path"), str) or not isinstance(record.get("sha256"), str):
                        raise SystemExit("invalid bundle file record")
                    record_path = record["path"]
                    if record_path in seen:
                        raise SystemExit(f"duplicate bundle file record: {record_path}")
                    seen.add(record_path)
                    path = _manifest_record_path(root, record_path)
                    if not path.is_file() or sha256_file(path) != record["sha256"]:
                        raise SystemExit(f"bundle integrity failure: {record_path}")
                    if "size" in record and record["size"] != path.stat().st_size:
                        raise SystemExit(f"bundle size mismatch: {record_path}")
                if LAUNCHER_PATH not in seen:
                    raise SystemExit(f"bundle launcher is not covered by manifest: {LAUNCHER_PATH}")
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
        if any(part in EXCLUDED_PARTS for part in rel.parts) or (item.is_file() and item.name in MUTABLE_FILE_NAMES):
            continue
        target = destination / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)

def _copy_payload(payload: Path, version_root: Path) -> None:
    version_root.mkdir(parents=True, exist_ok=True)
    for name in (".ai-harness", "portable", "dashboard"):
        source = payload / name
        if source.is_dir():
            _copy_tree_without_mutable_state(source, version_root / name)
    skill = payload / "skills" / "ai-coding-orchestrator"
    if skill.is_dir():
        _copy_tree_without_mutable_state(skill, version_root / "skills" / "ai-coding-orchestrator")
    launcher = payload.parent / LAUNCHER_PATH
    if not launcher.is_file():
        raise SystemExit("bundle launcher is missing: auren_cli.py")
    shutil.copy2(launcher, version_root / LAUNCHER_PATH)

def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)

def _set_current(root: Path, version_root: Path) -> None:
    current = root / "current"
    staged = root / ".current.tmp"
    if staged.exists() or staged.is_symlink():
        shutil.rmtree(staged) if staged.is_dir() and not staged.is_symlink() else staged.unlink()
    try:
        staged.symlink_to(version_root, target_is_directory=True)
        if current.exists() or current.is_symlink():
            shutil.rmtree(current) if current.is_dir() and not current.is_symlink() else current.unlink()
        staged.replace(current)
    except OSError:
        temp_dir = root / ".current-copy.tmp"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        _copy_tree_without_mutable_state(version_root, temp_dir)
        if current.exists() or current.is_symlink():
            shutil.rmtree(current) if current.is_dir() and not current.is_symlink() else current.unlink()
        temp_dir.replace(current)

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
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)

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
        temp = Path(tempfile.mkdtemp(prefix="auren-install-"))
        try:
            with zipfile.ZipFile(native_bundle) as archive:
                safe_extract(archive, temp)
            existing = version_root / "install.json"
            if existing.is_file():
                prior = json.loads(existing.read_text(encoding="utf-8"))
                if prior.get("source_commit") != manifest["source_commit"] or prior.get("bundle_sha256") != bundle_hash:
                    raise SystemExit(f"build directory {version_root.name} is already pinned to different content; refusing overwrite")
            elif version_root.exists():
                raise SystemExit(f"version directory exists without a pin: {version_root}")
            _copy_payload(temp / PAYLOAD_ROOT, version_root)
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
            _atomic_json(version_root / "install.json", record)
            _set_current(root, version_root)
            _atomic_json(root / "active.json", record)
            with (root / "history.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "activate", **record}) + "\n")
            _sync_skills(temp / PAYLOAD_ROOT / "skills" / "ai-coding-orchestrator", install_skill)
            print(f"Installed and pinned AUREN {version} ({manifest['source_commit'][:12]})")
            print(f"Build: {version_root.name}")
            print(f"Active installation: {root / 'current'}")
            print("Repository isolation: ON")
            return root / "current"
        finally:
            shutil.rmtree(temp, ignore_errors=True)
    finally:
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)

def _http_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "auren-portable"})
    with urllib.request.urlopen(request, timeout=20) as response:
        value = json.loads(response.read().decode("utf-8"))
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
    active = json.loads((root / "active.json").read_text(encoding="utf-8")) if (root / "active.json").is_file() else {}
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
    version_root = root / "versions" / install_root
    if not (version_root / "install.json").is_file() or not (version_root / LAUNCHER_PATH).is_file():
        raise SystemExit(f"rollback target is incomplete: {version_root}")
    _set_current(root, version_root)
    _atomic_json(root / "active.json", target)
    with (root / "history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "rollback", **target}) + "\n")
    _sync_skills(version_root / "skills" / "ai-coding-orchestrator", "agents")
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
    console_parser = sub.add_parser("console")
    console_parser.add_argument("--host", default="127.0.0.1")
    console_parser.add_argument("--port", type=int, default=0)
    console_parser.add_argument("--open", dest="open_browser", action="store_true")
    console_parser.add_argument("--auren-home", type=Path, default=None)
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
    elif args.command == "console":
        from .auren_console import serve_console
        if args.host not in {"127.0.0.1", "localhost", "::1"}:
            raise SystemExit("console host must be loopback")
        serve_console(args.auren_home, args.host, args.port, args.open_browser, True)
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
