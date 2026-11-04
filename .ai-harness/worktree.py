#!/usr/bin/env python3
"""Safe, resumable Git worktree helper with durable task and scope identity."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
WORKTREES = ROOT / ".ai-harness" / "worktrees"
STATE = ROOT / ".ai-harness" / "worktrees.json"
_SCHEMA = 2
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def run(command: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        result = subprocess.run(command, cwd=ROOT if cwd is None else cwd, text=True, capture_output=True, check=False, shell=False)
    except OSError as exc:
        return 127, f"ERROR: {exc}"
    return result.returncode, (result.stdout or result.stderr).strip()


def _commit(base: str) -> str:
    if not isinstance(base, str) or not base.strip() or len(base) > 200:
        raise ValueError("base must be a non-empty Git revision")
    code, value = run(["git", "rev-parse", "--verify", f"{base}^{{commit}}"])
    if code or not _SHA.fullmatch(value):
        raise ValueError(f"base does not resolve to a full commit SHA: {value}")
    return value


def _paths(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("owned_paths must be a sequence of repository-relative paths")
    result = []
    for value in values:
        if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
            raise ValueError("owned paths must be non-empty POSIX paths")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"unsafe owned path: {value!r}")
        normalized = path.as_posix()
        if normalized != value.rstrip("/"):
            raise ValueError(f"owned path must be normalized: {value!r}")
        if normalized not in result:
            result.append(normalized)
    if not result:
        raise ValueError("task-bound worktrees require at least one owned path")
    return tuple(sorted(result))


def load() -> dict[str, dict[str, Any]]:
    if not STATE.exists():
        return {}
    if STATE.is_symlink():
        raise PermissionError("worktree registry must not be a symlink")
    try:
        value = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("worktree registry is corrupt; refusing to treat it as empty") from exc
    if not isinstance(value, dict):
        raise ValueError("worktree registry root must be an object")
    if "worktrees" not in value:
        records = value  # legacy name -> record format
    else:
        if value.get("schema_version") != _SCHEMA or not isinstance(value["worktrees"], dict):
            raise ValueError("unsupported worktree registry schema")
        records = value["worktrees"]
    if any(not isinstance(k, str) or not isinstance(v, dict) for k, v in records.items()):
        raise ValueError("worktree registry contains an invalid record")
    return records


def save(records: dict[str, dict[str, Any]]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    if STATE.is_symlink():
        raise PermissionError("worktree registry must not be a symlink")
    payload = json.dumps({"schema_version": _SCHEMA, "worktrees": records}, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=STATE.name + ".", suffix=".tmp", dir=STATE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, STATE)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _managed_path(record: dict[str, Any]) -> Path:
    raw = Path(record.get("path", ""))
    if not str(raw) or raw.is_symlink():
        raise PermissionError("persisted worktree path is missing or a symlink")
    root = WORKTREES.resolve()
    resolved = raw.resolve()
    if resolved == root or root not in resolved.parents:
        raise PermissionError("persisted worktree path escapes configured root")
    return resolved

def safe_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("worktree name is required")
    result = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")[:70]
    if not result:
        raise ValueError("worktree name must contain a safe identifier")
    return result


def create(
    name: str, base: str = "HEAD", detached: bool = False, *,
    task_id: str | None = None, intent_digest: str | None = None,
    budget_digest: str | None = None, owned_paths: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    key = safe_name(name)
    records = load()
    if key in records:
        raise ValueError(f"worktree identity already exists: {key}; resume or remove it explicitly")
    base_sha = _commit(base)
    if task_id is not None:
        if not isinstance(task_id, str) or not _TOKEN.fullmatch(task_id):
            raise ValueError("task_id must be a bounded identifier token")
        if not isinstance(intent_digest, str) or not _DIGEST.fullmatch(intent_digest):
            raise ValueError("task-bound worktrees require an intent SHA-256 digest")
        if not isinstance(budget_digest, str) or not _DIGEST.fullmatch(budget_digest):
            raise ValueError("task-bound worktrees require a budget SHA-256 digest")
        paths = _paths(owned_paths)
    else:
        if intent_digest is not None or budget_digest is not None or owned_paths:
            raise ValueError("intent, budget and owned paths require a task_id")
        paths = ()
    WORKTREES.mkdir(parents=True, exist_ok=True)
    if WORKTREES.is_symlink():
        raise PermissionError("worktree root must not be a symlink")
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = WORKTREES / f"{key}-{stamp}"
    branch = None if detached else f"ai-harness/{key}-{dt.datetime.now(dt.UTC).strftime('%Y%m%d%H%M%S')}"
    record = {
        "name": key, "path": str(path), "base": base, "base_sha": base_sha,
        "branch": branch, "task_id": task_id, "intent_digest": intent_digest,
        "budget_digest": budget_digest, "owned_paths": list(paths),
        "status": "creating", "created_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    records[key] = record
    save(records)  # durable reservation before creating the Git worktree
    command = ["git", "worktree", "add"]
    command += ["--detach", str(path), base_sha] if detached else ["-b", branch, str(path), base_sha]
    code, output = run(command)
    if code:
        record["status"] = "creation_failed"
        record["error"] = output[:1000]
        save(records)
        raise RuntimeError(output)
    record["status"] = "active"
    record["updated_at"] = dt.datetime.now(dt.UTC).isoformat()
    save(records)
    return dict(record)


def resume(
    name: str, *, task_id: str, intent_digest: str, budget_digest: str,
    owned_paths: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    key = safe_name(name)
    record = load().get(key)
    if not record:
        raise KeyError(f"Unknown worktree: {key}")
    if record.get("status") not in {"active", "creating", "creation_failed"}:
        raise RuntimeError(f"worktree cannot resume from state {record.get('status')}")
    if record.get("task_id") != task_id or record.get("intent_digest") != intent_digest:
        raise PermissionError("worktree task or intent binding changed")
    if record.get("budget_digest") != budget_digest:
        raise PermissionError("worktree budget binding changed")
    if tuple(record.get("owned_paths", ())) != _paths(owned_paths):
        raise PermissionError("worktree owned-path scope changed")
    path = _managed_path(record)
    code, top = run(["git", "-C", str(path), "rev-parse", "--show-toplevel"])
    if code or Path(top).resolve() != path:
        raise RuntimeError("persisted worktree is missing or invalid")
    code, listing = run(["git", "worktree", "list", "--porcelain"])
    if code or f"worktree {path}" not in listing.splitlines():
        raise PermissionError("persisted path is not registered to this source checkout")
    code, head = run(["git", "-C", str(path), "rev-parse", "--verify", "HEAD^{commit}"])
    if code or head != record.get("base_sha"):
        raise RuntimeError("persisted worktree base differs; refusing unsafe resume")
    code, changed = run(["git", "-C", str(path), "diff", "--name-only", "-z", record["base_sha"], "--"])
    if code:
        raise RuntimeError("unable to inspect persisted worktree changes")
    code, untracked = run(["git", "-C", str(path), "ls-files", "--others", "--exclude-standard", "-z"])
    if code:
        raise RuntimeError("unable to inspect persisted worktree untracked files")
    touched = {item for item in (changed + "\x00" + untracked).split("\x00") if item}
    outside = [item for item in touched if not any(
        item == owned or item.startswith(owned.rstrip("/") + "/")
        for owned in record["owned_paths"]
    )]
    if outside:
        raise PermissionError("persisted worktree contains out-of-scope changes: " + ", ".join(sorted(outside)))
    return dict(record)


def remove(name: str, force: bool = False, *, worker_stopped: bool = False) -> dict[str, Any]:
    key = safe_name(name)
    records = load()
    record = records.get(key)
    if not record:
        raise KeyError(f"Unknown worktree: {key}")
    if record.get("task_id") and worker_stopped is not True:
        raise PermissionError("task-bound worktree removal requires explicit verified worker-stop confirmation")
    path = _managed_path(record)
    code, listing = run(["git", "worktree", "list", "--porcelain"])
    if code or f"worktree {path}" not in listing.splitlines():
        raise PermissionError("persisted path is not registered to this source checkout")
    command = ["git", "worktree", "remove"]
    if force:
        command.append("--force")
    command.append(str(path))
    code, output = run(command)
    if code:
        raise RuntimeError(output)
    record["status"] = "cleaned"
    record["updated_at"] = dt.datetime.now(dt.UTC).isoformat()
    record.pop("error", None)
    records[key] = record
    save(records)
    return dict(record)


def list_worktrees() -> list[dict[str, str]]:
    code, output = run(["git", "worktree", "list", "--porcelain"])
    if code:
        raise RuntimeError(output)
    records, current = [], {}
    for line in output.splitlines():
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        records.append(current)
    return records


def reconcile() -> dict[str, list[str]]:
    records = load()
    managed_root = WORKTREES.resolve()
    registered = {
        str(Path(record["path"]).resolve())
        for record in records.values() if record.get("status") != "cleaned"
    }
    code, output = run(["git", "worktree", "list", "--porcelain"])
    if code:
        raise RuntimeError(output)
    actual = {
        str(Path(line.partition(" ")[2]).resolve())
        for line in output.splitlines()
        if line.startswith("worktree ")
        and managed_root in Path(line.partition(" ")[2]).resolve().parents
    }
    return {"missing": sorted(registered - actual), "orphaned": sorted(actual - registered)}


def main() -> int:
    parser = argparse.ArgumentParser(description="AI harness Git worktree helper")
    sub = parser.add_subparsers(dest="action", required=True)
    cp = sub.add_parser("create")
    cp.add_argument("name")
    cp.add_argument("--base", default="HEAD")
    cp.add_argument("--detached", action="store_true")
    cp.add_argument("--task-id")
    cp.add_argument("--intent-digest")
    cp.add_argument("--budget-digest")
    cp.add_argument("--owned-path", action="append", default=[])
    rp = sub.add_parser("resume")
    rp.add_argument("name")
    rp.add_argument("--task-id", required=True)
    rp.add_argument("--intent-digest", required=True)
    rp.add_argument("--budget-digest", required=True)
    rp.add_argument("--owned-path", action="append", required=True)
    rm = sub.add_parser("remove")
    rm.add_argument("name")
    rm.add_argument("--force", action="store_true")
    rm.add_argument("--worker-stopped", action="store_true")
    sub.add_parser("list")
    sub.add_parser("reconcile")
    args = parser.parse_args()
    try:
        if args.action == "create":
            result = create(
                args.name, args.base, args.detached, task_id=args.task_id,
                intent_digest=args.intent_digest, budget_digest=args.budget_digest,
                owned_paths=args.owned_path,
            )
        elif args.action == "resume":
            result = resume(
                args.name, task_id=args.task_id, intent_digest=args.intent_digest,
                budget_digest=args.budget_digest, owned_paths=args.owned_path,
            )
        elif args.action == "remove":
            result = remove(args.name, args.force, worker_stopped=args.worker_stopped)
        elif args.action == "reconcile":
            result = reconcile()
        else:
            result = list_worktrees()
        print(json.dumps(result, indent=2))
        return 0
    except (RuntimeError, KeyError, ValueError, PermissionError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
