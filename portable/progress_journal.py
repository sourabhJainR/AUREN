"""Durable, truthful progress journal for long-running AUREN tasks."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import errno, json, math, os, re, threading, time
from pathlib import Path
from typing import Any

from contextlib import contextmanager

@contextmanager
def _journal_file_lock(path: Path, *, timeout_seconds: float = 2.0):
    """Serialize journal writes with a finite lock-acquisition budget."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("journal lock timeout must be finite and positive")
    deadline = time.monotonic() + float(timeout_seconds)

    def acquire_bounded(acquire):
        while True:
            try:
                acquire()
                return
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("progress journal lock acquisition timed out") from exc
                time.sleep(min(0.01, remaining))
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt
            lock_file.seek(0)
            if lock_file.read(1) == b"":
                lock_file.seek(0)
                lock_file.write(b"\\0")
                lock_file.flush()
            lock_file.seek(0)
            acquire_bounded(lambda: (lock_file.seek(0), msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)))
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire_bounded(lambda: fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB))
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

TERMINAL = {"completed", "failed", "cancelled"}
STATES = {"queued", "running", "blocked", "recovering", "completed", "failed", "cancelled"}
REDACTORS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*)[^\s,;]+"),
)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _safe(value: str, limit: int = 500) -> str:
    value = str(value).replace("\x00", "")[:limit]
    for pattern in REDACTORS:
        value = pattern.sub(r"\1[REDACTED]", value)
    return value

@dataclass(frozen=True)
class ProgressEvent:
    sequence: int
    task_id: str
    event: str
    state: str
    stage: str
    message: str
    timestamp: str
    completed_units: int | None = None
    total_units: int | None = None
    evidence_ref: str | None = None

class ProgressJournal:
    """Append-only JSONL event journal; progress reflects observed work only."""
    def __init__(self, path: str | Path, *, lock_timeout_seconds: float = 2.0):
        if isinstance(lock_timeout_seconds, bool) or not isinstance(lock_timeout_seconds, (int, float)) or not math.isfinite(lock_timeout_seconds) or lock_timeout_seconds <= 0:
            raise ValueError("lock_timeout_seconds must be finite and positive")
        self.path = Path(path)
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self._lock = threading.RLock()

    def _events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = []
        with self.path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict) and row.get("task_id"):
                        rows.append(row)
                except json.JSONDecodeError:
                    continue
        return rows

    def record(self, task_id: str, event: str, *, state: str = "running",
               stage: str = "execution", message: str = "",
               completed_units: int | None = None, total_units: int | None = None,
               evidence_ref: str | None = None, timestamp: str | None = None) -> ProgressEvent:
        if not task_id or not task_id.strip():
            raise ValueError("task_id must be non-empty")
        if state not in STATES:
            raise ValueError(f"unsupported state: {state}")
        if completed_units is not None and completed_units < 0:
            raise ValueError("completed_units cannot be negative")
        if total_units is not None and total_units < 0:
            raise ValueError("total_units cannot be negative")
        if completed_units is not None and total_units is not None and completed_units > total_units:
            raise ValueError("completed_units cannot exceed total_units")
        if state == "completed" and completed_units is not None and total_units is not None and completed_units != total_units:
            raise ValueError("completed task must account for all declared units")
        with self._lock, _journal_file_lock(self.path, timeout_seconds=self.lock_timeout_seconds):
            task_rows = [r for r in self._events() if r["task_id"] == task_id]
            if task_rows and task_rows[-1]["state"] in TERMINAL:
                raise ValueError("cannot append progress to a terminal task")
            row = ProgressEvent(
                sequence=task_rows[-1]["sequence"] + 1 if task_rows else 1,
                task_id=task_id, event=_safe(event, 80), state=state,
                stage=_safe(stage, 120), message=_safe(message),
                timestamp=timestamp or _now(), completed_units=completed_units,
                total_units=total_units, evidence_ref=_safe(evidence_ref, 1000) if evidence_ref else None,
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(asdict(row), sort_keys=True, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return row

    def heartbeat(self, task_id: str, *, stage: str, message: str = "Still working",
                  timestamp: str | None = None) -> ProgressEvent:
        return self.record(task_id, "heartbeat", stage=stage, message=message, timestamp=timestamp)

    def snapshot(self, task_id: str, *, now: datetime | None = None,
                 stale_after_seconds: int = 60) -> dict[str, Any]:
        rows = [r for r in self._events() if r["task_id"] == task_id]
        if not rows:
            return {"task_id": task_id, "state": "unknown", "has_progress": False}
        latest = rows[-1]
        last = datetime.fromisoformat(latest["timestamp"].replace("Z", "+00:00"))
        age = max(0.0, ((now or datetime.now(timezone.utc)) - last).total_seconds())
        stale = latest["state"] not in TERMINAL and age > stale_after_seconds
        done, total = latest.get("completed_units"), latest.get("total_units")
        percent = round(100 * done / total, 1) if done is not None and total is not None and total > 0 else None
        return {
            "task_id": task_id, "state": "stale" if stale else latest["state"],
            "reported_state": latest["state"], "stage": latest["stage"],
            "message": latest["message"], "sequence": latest["sequence"],
            "last_update": latest["timestamp"], "seconds_since_update": round(age, 1),
            "stale": stale, "progress_percent": percent, "completed_units": done,
            "total_units": total, "evidence_ref": latest.get("evidence_ref"),
            "event_count": len(rows),
            "next_action": "inspect worker health and deadline; recover or fail visibly" if stale else None,
        }

    def list_snapshots(self, *, now: datetime | None = None, stale_after_seconds: int = 60) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(r["task_id"] for r in self._events()))
        return [self.snapshot(task_id, now=now, stale_after_seconds=stale_after_seconds) for task_id in ids]
