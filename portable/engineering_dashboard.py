"""AUREN engineering observability snapshot and local dashboard data service."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import time
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from contextlib import closing
from typing import Any, Iterable, Mapping
from .repository_intelligence import RepositoryIntelligence
from .engineering_plan import EngineeringPlan, EngineeringPlanRun
from .progress_journal import ProgressJournal, _journal_file_lock, _safe
from .identity_compat import canonical_auren_home
from .workflow_profile import WorkflowProfile

def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def _json(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return str(value)


_EVENT_MAX_BYTES = 64 * 1024
_SENSITIVE_EVENT_KEY = re.compile(
    r"(?i)(?:secret|token|password|api[_-]?key|authorization|credential|private[_-]?key)"
)


def _safe_event_value(value: Any, *, depth: int = 0, key: str = "") -> Any:
    """Redact and bound event data without serializing arbitrary object reprs."""
    if key and _SENSITIVE_EVENT_KEY.search(key):
        return "[REDACTED]"
    if depth > 8:
        return "[DEPTH_LIMIT]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[NON_FINITE]"
    if isinstance(value, str):
        return _safe(value, limit=2000)
    if isinstance(value, Path):
        return _safe(str(value), limit=2000)
    if isinstance(value, Mapping):
        result = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= 100:
                result["[TRUNCATED]"] = True
                break
            safe_key = _safe(str(raw_key), limit=128)
            result[safe_key] = _safe_event_value(item, depth=depth + 1, key=safe_key)
        return result
    if isinstance(value, (list, tuple)):
        return [
            _safe_event_value(item, depth=depth + 1)
            for item in value[:100]
        ] + (["[TRUNCATED]"] if len(value) > 100 else [])
    return f"[unsupported:{type(value).__name__}]"

@dataclass(frozen=True)
class DashboardSnapshot:
    generated_at: str
    project: str
    repository: dict[str, Any]
    executions: tuple[dict[str, Any], ...]
    plan_runs: tuple[dict[str, Any], ...]
    tasks: dict[str, Any]
    learnings: dict[str, Any]
    findings: dict[str, Any]
    usage: dict[str, Any]
    quality: dict[str, Any]
    benchmarks: dict[str, Any]
    research: dict[str, Any]
    tests: dict[str, Any]
    graph: dict[str, Any]
    def as_dict(self) -> dict[str, Any]:
        return _json(asdict(self))

class EngineeringDashboard:
    """Observation-only dashboard over existing AUREN state."""
    def __init__(self, project_root: str | Path, *, project: str | None = None) -> None:
        self.root = Path(project_root).resolve()
        self.project = project or self.root.name
        self.state_dir = self.root / ".auren" / "dashboard"
        self.event_path = self.state_dir / "events.jsonl"
        self._repo_cache = None
        self._cache_seconds = 5.0

    def record_event(self, *, run_id: str, event: str, status: str = "running",
                     task_id: str = "", detail: str = "", started_at: str = "",
                     finished_at: str = "", duration_ms: int | None = None,
                     metadata: dict[str, Any] | None = None) -> None:
        for name, value in (("run_id", run_id), ("event", event), ("status", status),
                            ("task_id", task_id), ("detail", detail),
                            ("started_at", started_at), ("finished_at", finished_at)):
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a string")
        if not run_id.strip() or not event.strip():
            raise ValueError("run_id and event are required")
        if duration_ms is not None and (type(duration_ms) is not int or duration_ms < 0):
            raise ValueError("duration_ms must be a non-negative integer or None")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping or None")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": _utc(), "run_id": _safe(run_id.strip(), limit=256),
            "event": _safe(event.strip(), limit=256),
            "status": _safe(status.strip() or "running", limit=80),
            "task_id": _safe(task_id.strip(), limit=256),
            "detail": _safe(detail, limit=2000),
            "started_at": _safe(started_at, limit=128),
            "finished_at": _safe(finished_at, limit=128),
            "duration_ms": duration_ms,
            "metadata": _safe_event_value(metadata or {}),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > _EVENT_MAX_BYTES:
            raise ValueError("dashboard event exceeds the 64 KiB encoded size limit")
        # One cross-process lock covers append, flush and fsync. A dashboard
        # projection is not canonical state, but its event lines must not interleave.
        with _journal_file_lock(self.event_path, timeout_seconds=2.0):
            with self.event_path.open("a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def snapshot(self) -> DashboardSnapshot:
        repo = self._repository()
        dbs = self._sqlite_files()
        tables = self._table_counts(dbs)
        events = self._events()
        tests = self._tests_snapshot(events)
        plan_runs = self._plan_runs(dbs)
        return DashboardSnapshot(
            generated_at=_utc(), project=self.project,
            repository={"root": str(self.root), "snapshot": repo.digest(),
                        "files": len(repo.files),
                        "symbols": sum(len(f.symbols) for f in repo.files.values()),
                        "edges": len(repo.edges)},
            executions=self._executions(events, dbs, plan_runs),
            plan_runs=plan_runs,
            tasks=self._tasks(tables, events, plan_runs),
            learnings=self._learnings(tables),
            findings=self._findings(tables, events),
            usage=self._usage(tables, events, dbs),
            quality=self._quality(repo, tests, tables),
            benchmarks=self._benchmarks(tables),
            research=self._research(tables),
            tests=tests,
            graph=self._graph(repo),
        )

    def _repository(self):
        now = time.monotonic()
        if self._repo_cache is None or now - self._repo_cache[0] >= self._cache_seconds:
            self._repo_cache = (now, RepositoryIntelligence.build(self.root, ignores={".aer", ".auren", "state"}))
        return self._repo_cache[1]

    def _sqlite_files(self) -> tuple[Path, ...]:
        candidates = []
        for base in (self.root / ".auren", self.root / "state", self.root / ".ai-harness", self.root):
            if not base.exists():
                continue
            try:
                candidates.extend(p for p in base.rglob("*")
                                 if p.is_file() and p.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
                                 and ".git" not in p.parts)
            except OSError:
                continue
        seen, result = set(), []
        for path in sorted(candidates):
            key = str(path)
            if key not in seen:
                seen.add(key); result.append(path)
        return tuple(result)

    def _table_counts(self, paths: Iterable[Path]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for path in paths:
            try:
                uri = f"file:{path.as_posix()}?mode=ro"
                with closing(sqlite3.connect(uri, uri=True, timeout=0.5)) as db, db:
                    names = [r[0] for r in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                    for name in names:
                        try:
                            count = int(db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
                        except sqlite3.Error:
                            continue
                        counts[name] = counts.get(name, 0) + count
            except (OSError, sqlite3.Error):
                continue
        return counts

    def _events(self) -> tuple[dict[str, Any], ...]:
        if not self.event_path.exists():
            return ()
        rows = []
        try:
            lines = self.event_path.read_text(encoding="utf-8").splitlines()[-5000:]
        except OSError:
            return ()
        for line in lines:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            except json.JSONDecodeError:
                continue
        return tuple(rows)

    @staticmethod
    def _sum(tables: dict[str, int], needles: Iterable[str]) -> int:
        return sum(v for k, v in tables.items() if any(n in k.lower() for n in needles))


    @staticmethod
    def _plan_checkpoint_state(
        paths: Iterable[Path], store_path: Path, plan: EngineeringPlan, run: EngineeringPlanRun,
    ) -> tuple[bool, bool]:
        """Return whether a checkpoint claims this store and whether it verifies this run."""
        matches = []
        expected_store_path = str(store_path.resolve())
        for candidate in paths:
            try:
                uri = f"{candidate.resolve().as_uri()}?mode=ro"
                with closing(sqlite3.connect(uri, uri=True, timeout=0.5)) as db, db:
                    exists = db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_checkpoints'"
                    ).fetchone()
                    if not exists:
                        continue
                    row = db.execute(
                        "SELECT status, head_sha, payload_json FROM recovery_checkpoints "
                        "WHERE task_id=? AND phase='engineering_plan'",
                        (plan.run_id,),
                    ).fetchone()
                if row is None:
                    continue
                try:
                    payload = json.loads(row[2])
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if (
                    payload.get("contract") != "auren-engineering-plan-checkpoint-v1"
                    or payload.get("store_path") != expected_store_path
                ):
                    continue
                matches.append((row, payload))
            except (OSError, sqlite3.Error):
                continue
        if not matches:
            return False, False
        row, payload = matches[0]
        # Duplicate discovery is safe only when every checkpoint bound to this
        # store has identical status, source, and payload.
        if any(other_row != row or other_payload != payload for other_row, other_payload in matches[1:]):
            return True, False
        status, head_sha, _payload_json = row
        if status not in {"pending", "in_progress", "verified", "blocked", "rolled_back"}:
            return True, False
        if head_sha != plan.source_revision or status == "rolled_back":
            return True, False
        task_statuses = [item.status for item in run.tasks]
        all_passed = bool(task_statuses) and all(value == "passed" for value in task_statuses)
        any_blocked = any(value == "blocked" for value in task_statuses)
        if status == "verified" and not all_passed:
            return True, False
        if status == "blocked" and not any_blocked:
            return True, False
        if status in {"pending", "in_progress"} and all_passed:
            return True, False
        verified = (
            payload.get("plan_digest") == plan.digest
            and payload.get("run_digest") == run.digest
            and type(payload.get("run_revision")) is int
            and payload.get("run_revision") == run.revision
            and payload.get("source_revision") == plan.source_revision
            and isinstance(payload.get("next_safe_action"), str)
            and bool(payload.get("next_safe_action", "").strip())
        )
        return True, verified

    def _plan_runs(self, paths: Iterable[Path]) -> tuple[dict[str, Any], ...]:
        """Project persisted plan/run state without treating corrupt state as success.

        The dashboard is a read-only observer. It verifies the stored plan and run
        digests and prefers only the store bound to a compatible recovery checkpoint.
        """
        paths = tuple(paths)
        candidates: dict[str, list[dict[str, Any]]] = {}
        for path in paths:
            try:
                uri = f"file:{path.as_posix()}?mode=ro"
                with closing(sqlite3.connect(uri, uri=True, timeout=0.5)) as db, db:
                    table = db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='engineering_plan_runs'"
                    ).fetchone()
                    if not table:
                        continue
                    columns = {row[1] for row in db.execute("PRAGMA table_info(engineering_plan_runs)")}
                    required = {"run_id", "schema_version", "plan_digest", "plan_json", "run_json", "run_digest", "updated_at"}
                    if not required.issubset(columns):
                        continue
                    rows = db.execute(
                        "SELECT run_id, schema_version, plan_digest, plan_json, run_json, run_digest, updated_at "
                        "FROM engineering_plan_runs ORDER BY updated_at DESC LIMIT 100"
                    ).fetchall()
            except (OSError, sqlite3.Error):
                continue
            for row in rows:
                run_id = str(row[0])
                checkpoint_claimed = False
                try:
                    plan_payload = json.loads(row[3])
                    run_payload = json.loads(row[4])
                    plan = EngineeringPlan.from_dict(plan_payload)
                    run = EngineeringPlanRun.from_dict(run_payload)
                    if (int(row[1]) != 2 or plan.run_id != run_id or plan.digest != row[2]
                            or run.digest != row[5] or run.plan_digest != plan.digest):
                        raise ValueError("persisted plan/run identity or digest mismatch")
                    run.verify_against(plan)
                    checkpoint_claimed, checkpoint_verified = self._plan_checkpoint_state(paths, path, plan, run)
                    if not checkpoint_verified:
                        raise ValueError("canonical recovery checkpoint missing or mismatched")
                    counts: dict[str, int] = {}
                    for progress in run.tasks:
                        counts[progress.status] = counts.get(progress.status, 0) + 1
                    total = len(run.tasks)
                    passed = counts.get("passed", 0)
                    blocked = counts.get("blocked", 0)
                    failed = counts.get("failed", 0)
                    cancelled = counts.get("cancelled", 0)
                    if total and passed == total:
                        status = "completed"
                    elif failed:
                        status = "failed"
                    elif cancelled:
                        status = "cancelled"
                    elif blocked:
                        status = "blocked"
                    elif counts.get("running", 0) or counts.get("verifying", 0):
                        status = "running"
                    elif counts.get("ready", 0):
                        status = "ready"
                    else:
                        status = "planned"
                    detail = (f"{passed}/{total} tasks passed; {blocked} blocked; "
                              f"{failed} failed; revision {run.revision}")
                    projected = {
                        "run_id": run_id, "task_id": run_id, "goal": plan.goal,
                        "status": status, "detail": detail, "plan_digest": plan.digest,
                        "source_revision": plan.source_revision, "revision": run.revision,
                        "task_counts": counts,
                        "evidence_count": sum(len(progress.evidence_refs) for progress in run.tasks),
                        "updated_at": row[6],
                        "_checkpoint_claimed": checkpoint_claimed,
                        "_checkpoint_verified": True,
                        "_store_path": str(path.resolve()),
                    }
                except (ValueError, TypeError, KeyError, json.JSONDecodeError, AttributeError):
                    projected = {
                        "run_id": run_id, "task_id": run_id, "status": "unknown",
                        "detail": "Persisted plan/run state failed integrity validation; reconciliation required.",
                        "plan_digest": str(row[2]), "updated_at": row[6],
                        "_checkpoint_claimed": checkpoint_claimed,
                        "_checkpoint_verified": False,
                        "_store_path": str(path.resolve()),
                    }
                candidates.setdefault(run_id, []).append(projected)
        latest: dict[str, dict[str, Any]] = {}
        for run_id, items in candidates.items():
            claimed_paths = {item["_store_path"] for item in items if item.get("_checkpoint_claimed")}
            if len(claimed_paths) > 1:
                selected = {
                    "run_id": run_id, "task_id": run_id, "status": "unknown",
                    "detail": "Conflicting recovery checkpoints claim this plan identity; reconciliation required.",
                    "updated_at": max((item.get("updated_at", "") for item in items), key=str),
                }
            else:
                verified = [item for item in items if item.get("_checkpoint_verified")]
                if len(verified) == 1:
                    selected = verified[0]
                elif len(verified) > 1:
                    signatures = {
                        (
                            item.get("_store_path"), item.get("plan_digest"), item.get("revision"),
                            item.get("status"), tuple(sorted(item.get("task_counts", {}).items())),
                        )
                        for item in verified
                    }
                    if len(signatures) == 1:
                        selected = max(verified, key=lambda item: str(item.get("updated_at", "")))
                    else:
                        selected = {
                            "run_id": run_id, "task_id": run_id, "status": "unknown",
                            "detail": "Multiple verified plan stores disagree; reconciliation required.",
                            "updated_at": max((item.get("updated_at", "") for item in items), key=str),
                        }
                else:
                    claimed = [item for item in items if item.get("_checkpoint_claimed")]
                    selected = max(claimed or items, key=lambda item: str(item.get("updated_at", "")))
            latest[run_id] = {key: value for key, value in selected.items() if not key.startswith("_")}
        return tuple(sorted(latest.values(), key=lambda row: str(row.get("updated_at", "")), reverse=True)[:50])

    def _executions(self, events: tuple[dict[str, Any], ...], dbs: Iterable[Path],
                    plan_runs: Iterable[dict[str, Any]] = ()) -> tuple[dict[str, Any], ...]:
        # Durable plan/run records are authoritative for matching run IDs.
        # A stale event projection must not resurrect a run that the canonical
        # store has already marked completed, failed, blocked, cancelled, or unknown.
        canonical_plan_runs = {
            str(row["run_id"]): row
            for row in plan_runs
            if row.get("run_id")
        }
        latest = {}
        for row in events:
            run_id = str(row.get("run_id") or "")
            if run_id and run_id not in canonical_plan_runs:
                latest[run_id] = row
        active = [r for r in latest.values() if r.get("status") in {"running", "executing", "started"}]
        active.extend(self._active_episode_rows(dbs))
        active.extend(row for row in plan_runs if row.get("status") == "running")
        unique = {}
        for row in active:
            key = str(row.get("run_id") or row.get("episode_id") or "")
            if key:
                unique[key] = row
        return tuple(sorted(unique.values(), key=lambda r: str(r.get("updated_at") or r.get("timestamp") or ""), reverse=True)[:50])

    def _active_episode_rows(self, paths: Iterable[Path]) -> tuple[dict[str, Any], ...]:
        rows = []
        for path in paths:
            try:
                uri = f"file:{path.as_posix()}?mode=ro"
                with closing(sqlite3.connect(uri, uri=True, timeout=0.5)) as db, db:
                    table = db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='engineering_episodes'"
                    ).fetchone()
                    if not table:
                        continue
                    values = db.execute(
                        """SELECT episode_id,project,task_family,capability,state,iteration,
                                  updated_at,last_error
                           FROM engineering_episodes
                           WHERE state IN ('planned','running','verifying','learning')
                           ORDER BY updated_at DESC LIMIT 50"""
                    ).fetchall()
                    for row in values:
                        rows.append({
                            "run_id": row[0], "episode_id": row[0], "task_id": row[0],
                            "project": row[1], "task_family": row[2], "capability": row[3],
                            "status": row[4], "iteration": row[5], "updated_at": row[6],
                            "detail": row[7] or "",
                        })
            except (OSError, sqlite3.Error):
                continue
        return tuple(rows)

    def _tasks(self, tables, events, plan_runs=()):
        ids = {str(e.get("task_id")) for e in events if e.get("task_id")}
        experience = self._sum(tables, ("experience_history",))
        plan_rows = tuple(plan_runs)
        plan_task_counts = {}
        for row in plan_rows:
            for status, count in row.get("task_counts", {}).items():
                plan_task_counts[status] = plan_task_counts.get(status, 0) + count
        return {"observed": max(experience, len(ids)), "event_runs": len({e.get("run_id") for e in events if e.get("run_id")}),
                "completed_experience_records": experience, "plan_runs": len(plan_rows),
                "plan_tasks": sum(plan_task_counts.values()), "plan_task_counts": plan_task_counts,
                "progress": self._progress_snapshots()}

    def _workflow_profile_bindings(self) -> dict[str, dict[str, str]]:
        """Read only the safe profile summary for durable tasks; never expose payloads."""
        candidates = (
            self.root / ".auren" / "execution.sqlite3",
            canonical_auren_home() / "executive" / ".auren" / "execution.sqlite3",
        )
        found: dict[str, dict[str, str]] = {}
        seen: set[str] = set()
        for path in candidates:
            if not path.is_file():
                continue
            identity = str(path.resolve())
            if identity in seen:
                continue
            seen.add(identity)
            try:
                with closing(sqlite3.connect(str(path), timeout=1.0)) as db:
                    db.execute("PRAGMA query_only=ON")
                    table = db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
                    ).fetchone()
                    if table is None:
                        continue
                    columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
                    ordering = "updated_at DESC" if "updated_at" in columns else "rowid DESC"
                    rows = db.execute(f"SELECT id,payload FROM tasks ORDER BY {ordering} LIMIT 500").fetchall()
                for task_id, payload_json in rows:
                    try:
                        payload = json.loads(payload_json)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(payload, Mapping):
                        continue
                    profile_data = payload.get("_auren_workflow_profile")
                    digest = payload.get("_auren_workflow_profile_digest")
                    if not isinstance(profile_data, Mapping) or not isinstance(digest, str):
                        continue
                    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                        found.setdefault(str(task_id), {"binding_status": "invalid"})
                        continue
                    try:
                        profile = WorkflowProfile.from_mapping(profile_data)
                    except (TypeError, ValueError):
                        found.setdefault(str(task_id), {"binding_status": "invalid"})
                        continue
                    if profile.digest != digest:
                        found.setdefault(str(task_id), {"binding_status": "invalid"})
                        continue
                    summary = {
                        key: profile.as_dict()[key]
                        for key in (
                            "profile_version", "delivery_mode", "execution_mode", "approval_mode",
                            "review_mode", "communication_mode", "recovery_mode",
                        )
                    }
                    summary["profile_digest"] = digest
                    summary["binding_status"] = "verified"
                    found.setdefault(str(task_id), summary)
            except (OSError, sqlite3.Error):
                # Dashboard is a projection, not authority. Preserve the journal
                # snapshot if a task database is busy, unreadable, or incompatible.
                continue
        return found

    @staticmethod
    def _progress_status_category(state: Any) -> str:
        value = str(state or "unknown").strip().lower()
        if value in {"completed", "failed", "cancelled", "timed_out", "unscored", "stale", "unknown"}:
            return value
        if value in {"blocked", "blocked_reconciliation", "blocked_quota", "blocked_approval", "waiting_approval"}:
            return "blocked"
        if value in {"pending", "queued", "waiting_quota"}:
            return "queued"
        if value == "running":
            return "running"
        if value == "recovering":
            return "recovering"
        return "unknown"

    def _canonical_task_states(
        self, journal_task_ids: set[str] | None = None
    ) -> tuple[dict[str, dict[str, Any]], set[str], bool]:
        """Read task status from existing controller databases without mutating them."""
        candidates = (
            self.root / ".auren" / "execution.sqlite3",
            canonical_auren_home() / "executive" / ".auren" / "execution.sqlite3",
        )
        states: dict[str, dict[str, Any]] = {}
        conflicts: set[str] = set()
        unreadable_store = False
        for path in candidates:
            if not path.is_file():
                continue
            try:
                uri = f"file:{path.as_posix()}?mode=ro"
                with closing(sqlite3.connect(uri, uri=True, timeout=0.5)) as db:
                    db.execute("PRAGMA query_only=ON")
                    exists = db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
                    ).fetchone()
                    if exists is None:
                        continue
                    columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
                    if not {"id", "state"} <= columns:
                        unreadable_store = True
                        continue
                    updated_column = "updated_at" if "updated_at" in columns else "NULL"
                    ordering = "updated_at DESC" if "updated_at" in columns else "rowid DESC"
                    rows = db.execute(
                        f"SELECT id,state,{updated_column} FROM tasks ORDER BY {ordering} LIMIT 1000"
                    ).fetchall()
                    # Keep the recent-task view bounded, but always reconcile every
                    # task already present in the journal. Otherwise an old task
                    # outside the recent 1000 could retain a stale terminal status.
                    rows_by_id = {
                        str(row[0] or "").strip(): row
                        for row in rows
                        if str(row[0] or "").strip()
                    }
                    if journal_task_ids:
                        requested_ids = sorted({
                            str(task_id).strip()
                            for task_id in journal_task_ids
                            if str(task_id).strip()
                        })
                        for offset in range(0, len(requested_ids), 400):
                            batch = requested_ids[offset:offset + 400]
                            placeholders = ",".join("?" for _ in batch)
                            exact_rows = db.execute(
                                f"SELECT id,state,{updated_column} FROM tasks WHERE id IN ({placeholders})",
                                batch,
                            ).fetchall()
                            for row in exact_rows:
                                task_id = str(row[0] or "").strip()
                                if task_id:
                                    rows_by_id[task_id] = row
                    rows = list(rows_by_id.values())
            except (OSError, sqlite3.Error):
                # If a configured store exists but cannot be read, another store's
                # apparent terminal state is not enough to claim a verified outcome.
                unreadable_store = True
                continue
            for raw_id, raw_state, updated_at in rows:
                task_id = str(raw_id or "").strip()
                if not task_id:
                    continue
                candidate = {
                    "state": str(raw_state or "unknown").strip().lower() or "unknown",
                    "updated_at": str(updated_at or ""),
                }
                previous = states.get(task_id)
                if previous is None:
                    states[task_id] = candidate
                elif previous["state"] != candidate["state"]:
                    conflicts.add(task_id)
                elif candidate["updated_at"] > previous["updated_at"]:
                    states[task_id] = candidate
        return states, conflicts, unreadable_store

    def _progress_snapshots(self) -> list[dict[str, Any]]:
        """Project journal progress, reconciled against canonical task state."""
        home = canonical_auren_home()
        candidates = (
            self.root / ".auren" / "progress.jsonl",
            home / "executive" / ".auren" / "progress.jsonl",
        )
        snapshots: dict[str, dict[str, Any]] = {}
        seen_paths: set[str] = set()
        profile_bindings = self._workflow_profile_bindings()
        for path in candidates:
            key = str(path.resolve())
            if key in seen_paths or not path.is_file():
                continue
            seen_paths.add(key)
            try:
                rows = ProgressJournal(path).list_snapshots(stale_after_seconds=60)
            except Exception:
                rows = [{
                    "task_id": "progress-journal", "state": "unknown", "reported_state": "unknown",
                    "stage": "journal", "message": "Durable progress journal could not be read safely.",
                    "last_update": None, "seconds_since_update": None, "stale": False,
                    "progress_percent": None, "completed_units": None, "total_units": None,
                    "event_count": 0, "next_action": "inspect journal integrity before trusting progress",
                }]
            for row in rows:
                snapshots[str(row.get("task_id", "unknown"))] = row

        canonical_states, conflicts, unreadable_store = self._canonical_task_states(set(snapshots))
        final_states = {"completed", "failed", "cancelled", "timed_out", "unscored"}
        if unreadable_store:
            # A second configured store could contain a conflicting durable state.
            # Do not let journal-only rows or the readable store turn this into green.
            for task_id, row in snapshots.items():
                row["reported_state"] = row.get("reported_state", row.get("state", "unknown"))
                row["state"] = "unknown"
                row["canonical_state"] = "unknown"
                row["state_source"] = "unreadable_durable_store"
                row["status_category"] = "unknown"
                row["status_conflict"] = True
                row["stale"] = False
                row["next_action"] = "inspect unreadable canonical task store before trusting progress or retrying"
            for task_id, state_row in canonical_states.items():
                if task_id not in snapshots:
                    snapshots[task_id] = {
                        "task_id": task_id, "state": "unknown", "reported_state": None,
                        "canonical_state": "unknown", "state_source": "unreadable_durable_store",
                        "status_category": "unknown", "status_conflict": True, "has_progress": False,
                        "last_update": state_row.get("updated_at"), "seconds_since_update": None,
                        "stale": False, "progress_percent": None, "completed_units": None,
                        "total_units": None, "event_count": 0,
                        "next_action": "inspect unreadable canonical task store before trusting progress or retrying",
                    }
            snapshots.setdefault("canonical-task-store-integrity", {
                "task_id": "canonical-task-store-integrity", "state": "unknown",
                "reported_state": None, "canonical_state": "unknown",
                "state_source": "unreadable_durable_store", "status_category": "unknown",
                "status_conflict": True, "has_progress": False, "last_update": None,
                "seconds_since_update": None, "stale": False, "progress_percent": None,
                "completed_units": None, "total_units": None, "event_count": 0,
                "next_action": "inspect unreadable canonical task store before trusting progress or retrying",
            })
        else:
            for task_id in set(canonical_states) | conflicts:
                canonical = canonical_states.get(task_id, {}).get("state", "unknown")
                updated_at = canonical_states.get(task_id, {}).get("updated_at")
                row = snapshots.get(task_id)
                if task_id in conflicts:
                    row = row or {
                        "task_id": task_id, "reported_state": None, "has_progress": False,
                        "last_update": updated_at, "event_count": 0,
                    }
                    row["reported_state"] = row.get("reported_state", row.get("state"))
                    row["state"] = "unknown"
                    row["canonical_state"] = "unknown"
                    row["state_source"] = "conflicting_durable_stores"
                    row["status_category"] = "unknown"
                    row["status_conflict"] = True
                    row["stale"] = False
                    row["next_action"] = "reconcile conflicting durable task records before retry"
                    snapshots[task_id] = row
                    continue

                if row is None:
                    uncertain = canonical in final_states or canonical in {"running", "unknown"}
                    display_state = "unknown" if uncertain else ("queued" if canonical == "pending" else canonical)
                    row = {
                        "task_id": task_id,
                        "state": display_state,
                        "reported_state": None,
                        "canonical_state": canonical,
                        "state_source": "execution_controller",
                        "status_category": self._progress_status_category(display_state),
                        "status_conflict": uncertain,
                        "has_progress": False,
                        "last_update": updated_at,
                        "seconds_since_update": None,
                        "stale": False,
                        "progress_percent": None,
                        "completed_units": None,
                        "total_units": None,
                        "event_count": 0,
                        "next_action": (
                            "reconcile the missing progress receipt before reporting a terminal outcome"
                            if canonical in final_states else
                            "inspect worker lease and journal before reporting active execution"
                            if canonical == "running" else
                            "inspect the missing progress journal before trusting progress"
                        ),
                    }
                    snapshots[task_id] = row
                    continue

                reported = str(row.get("reported_state", row.get("state", "unknown")) or "unknown").lower()
                row["reported_state"] = reported
                row["canonical_state"] = canonical
                row["state_source"] = "execution_controller"
                row["status_conflict"] = False

                if canonical in final_states:
                    if reported == canonical:
                        row["state"] = canonical
                        row["stale"] = False
                        row["next_action"] = None
                    else:
                        row["state"] = "unknown"
                        row["stale"] = False
                        row["status_conflict"] = True
                        row["next_action"] = "reconcile the terminal task receipt and progress journal before reporting an outcome"
                elif canonical == "running":
                    if reported in final_states or reported == "unknown":
                        row["state"] = "unknown"
                        row["stale"] = False
                        row["status_conflict"] = True
                        row["next_action"] = "inspect the canonical task lease and reconcile contradictory progress before retry"
                    elif row.get("stale") or reported == "stale":
                        row["state"] = "stale"
                        row["stale"] = True
                        row["next_action"] = "inspect worker health and deadline; recover or fail visibly"
                    else:
                        row["state"] = "running"
                        row["stale"] = False
                        row["next_action"] = None
                else:
                    if reported in final_states:
                        row["state"] = "unknown"
                        row["stale"] = False
                        row["status_conflict"] = True
                        row["next_action"] = "reconcile contradictory terminal progress before reporting a result"
                    else:
                        row["state"] = "queued" if canonical == "pending" else canonical
                        row["stale"] = False
                        if self._progress_status_category(canonical) == "blocked":
                            row["next_action"] = "resolve the durable blocker before dispatch"
                row["status_category"] = self._progress_status_category(row.get("state"))

        for row in snapshots.values():
            row.setdefault("reported_state", row.get("state", "unknown"))
            row.setdefault("canonical_state", None)
            row.setdefault("state_source", "progress_journal")
            row.setdefault("status_conflict", False)
            row.setdefault("status_category", self._progress_status_category(row.get("state")))
        for task_id, row in snapshots.items():
            profile = profile_bindings.get(task_id)
            if profile is None:
                row["workflow_profile_status"] = "legacy-or-unavailable"
            elif profile.get("binding_status") == "verified":
                row["workflow_profile_status"] = "verified"
                row["workflow_profile"] = {
                    key: value for key, value in profile.items() if key != "binding_status"
                }
            else:
                row["workflow_profile_status"] = "invalid"
        return sorted(
            snapshots.values(),
            key=lambda row: str(row.get("last_update") or ""),
            reverse=True,
        )[:20]

    def _learnings(self, tables):
        return {"deferred_jobs": self._sum(tables, ("deferred_learning",)),
                "skill_epochs": self._sum(tables, ("skill_optimization_epochs",)),
                "regression_cases": self._sum(tables, ("regression_cases",)),
                "maintenance_receipts": self._sum(tables, ("maintenance_receipts",))}

    def _findings(self, tables, events):
        return {"observed": self._sum(tables, ("finding", "review")),
                "failures": self._sum(tables, ("failure",)),
                "dont_rules": self._sum(tables, ("failure_dont", "dont")),
                "event_failures": sum(1 for e in events if e.get("status") in {"failed", "error"})}

    def _usage(self, tables, events, dbs):
        task_rows = {}
        for event in events:
            task_id = str(event.get("task_id") or "").strip()
            if task_id:
                task_rows.setdefault(task_id, []).append(event)
        durations = []
        task_durations = []
        for task_id, rows in task_rows.items():
            values = [r.get("duration_ms") for r in rows
                      if isinstance(r.get("duration_ms"), (int, float)) and r.get("duration_ms") >= 0]
            duration = int(values[-1]) if values else self._elapsed_ms(rows[0].get("timestamp"), rows[-1].get("timestamp"))
            if duration is not None:
                durations.append(duration)
                task_durations.append({"task_id": task_id, "duration_ms": duration})
        task_durations.sort(key=lambda x: (-x["duration_ms"], x["task_id"]))
        return {"runs": len({e.get("run_id") for e in events if e.get("run_id")}),
                "events": len(events), "sqlite_stores": len(dbs),
                "average_task_duration_ms": round(sum(durations) / len(durations)) if durations else None,
                "total_task_duration_ms": sum(durations), "task_durations": task_durations[-100:]}

    @staticmethod
    def _elapsed_ms(start, end):
        if not start or not end:
            return None
        try:
            first = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            last = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
            return max(0, round((last - first).total_seconds() * 1000))
        except (TypeError, ValueError):
            return None

    def _quality(self, repo, tests, tables):
        source_files = len(repo.files)
        test_files = tests["files"]
        return {"source_files": source_files, "test_files": test_files,
                "test_file_ratio": round(test_files / source_files, 3) if source_files else 0.0,
                "graph_edges": len(repo.edges),
                "verified_records": self._sum(tables, ("verification", "evidence")),
                "note": "Repository indicators are descriptive. Recorded verification data is shown when present; no synthetic quality score is invented."}

    def _benchmarks(self, tables):
        return {"regression_cases": self._sum(tables, ("regression_cases",)),
                "validations": self._sum(tables, ("regression_validations",)),
                "benchmark_records": self._sum(tables, ("benchmark", "evaluation")),
                "skill_optimization_epochs": self._sum(tables, ("skill_optimization_epochs",))}

    def _research(self, tables):
        return {"research_records": self._sum(tables, ("research", "hypothesis", "information")),
                "capability_experiments": self._sum(tables, ("capability", "experiment"))}

    def _tests_snapshot(self, events=()):
        files, cases = [], 0
        test_root = self.root / "tests"
        paths = sorted(test_root.rglob("test_*.py")) if test_root.exists() else ()
        for path in paths:
            files.append(str(path.relative_to(self.root)).replace("\\", "/"))
            try:
                text = path.read_text(encoding="utf-8")
                cases += len(re.findall(r"^\s*(?:async\s+)?def\s+test_", text, re.M))
            except OSError:
                continue
        added = sum(int(e.get("metadata", {}).get("tests_added", 0))
                    for e in events if isinstance(e.get("metadata"), dict)
                    and str(e.get("metadata", {}).get("tests_added", "")).isdigit())
        return {"files": len(files), "cases": cases, "tests_added_observed": added, "paths": files[-100:]}

    def _graph(self, repo):
        by_kind = {}
        for edge in repo.edges:
            by_kind[edge.kind] = by_kind.get(edge.kind, 0) + 1
        connected = []
        for path in repo.files:
            degree = sum(1 for e in repo.edges if e.source_path == path or e.target_path == path)
            connected.append((path, degree))
        top_files = sorted(connected, key=lambda x: (-x[1], x[0]))[:24]
        edge_rows = [{"source": e.source_path, "target": e.target_path, "kind": e.kind,
                      "confidence": round(float(e.confidence), 3)} for e in repo.edges[:160]]
        return {"nodes": len(repo.files), "edges": len(repo.edges), "edge_kinds": by_kind,
                "top_files": top_files, "observed_edges": edge_rows}

__all__ = ["DashboardSnapshot", "EngineeringDashboard"]
