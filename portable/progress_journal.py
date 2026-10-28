"""Durable, truthful progress journal for long-running AUREN tasks."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import errno, json, math, os, re, threading, time
from pathlib import Path
from typing import Any, Mapping

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
            # Acquire before reading or writing. On Windows, probing byte zero
            # while another process holds the range lock can raise PermissionError
            # instead of the bounded lock error handled above.
            acquire_bounded(lambda: (lock_file.seek(0), msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)))
            try:
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.seek(0)
                    lock_file.write(b"\\0")
                    lock_file.flush()
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
    priority: int = 0
    parent_task_id: str | None = None
    decision_required: bool = False

class ProgressJournalCorruption(RuntimeError):
    """The append-only progress journal contains data that cannot be trusted."""


class ProgressJournal:
    """Append-only JSONL event journal; progress reflects observed work only."""
    def __init__(self, path: str | Path, *, lock_timeout_seconds: float = 2.0):
        if isinstance(lock_timeout_seconds, bool) or not isinstance(lock_timeout_seconds, (int, float)) or not math.isfinite(lock_timeout_seconds) or lock_timeout_seconds <= 0:
            raise ValueError("lock_timeout_seconds must be finite and positive")
        self.path = Path(path)
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self._lock = threading.RLock()

    def _events(self) -> list[dict[str, Any]]:
        """Read the complete journal or fail closed on any damaged record.

        Skipping a malformed line can hide an earlier state transition, reuse a
        sequence number, or make a damaged terminal record look non-terminal.
        Keep the original bytes for operator recovery; never silently repair or
        truncate the audit trail.
        """
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        required = {"sequence", "task_id", "event", "state", "stage", "message", "timestamp"}
        try:
            with self.path.open(encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ProgressJournalCorruption(
                            f"progress journal is corrupt at line {line_number}; writes and snapshots are blocked"
                        ) from exc
                    if not isinstance(row, dict) or not required.issubset(row):
                        raise ProgressJournalCorruption(
                            f"progress journal has an invalid record at line {line_number}; writes and snapshots are blocked"
                        )
                    if (
                        not isinstance(row.get("task_id"), str) or not row["task_id"].strip()
                        or isinstance(row.get("sequence"), bool)
                        or not isinstance(row.get("sequence"), int) or row["sequence"] < 1
                        or not isinstance(row.get("state"), str) or row["state"] not in STATES
                        or not isinstance(row.get("event"), str)
                        or not isinstance(row.get("stage"), str)
                        or not isinstance(row.get("message"), str)
                        or not isinstance(row.get("timestamp"), str)
                    ):
                        raise ProgressJournalCorruption(
                            f"progress journal has invalid field values at line {line_number}; writes and snapshots are blocked"
                        )
                    rows.append(row)
        except UnicodeDecodeError as exc:
            raise ProgressJournalCorruption(
                "progress journal is not valid UTF-8; writes and snapshots are blocked"
            ) from exc
        return rows

    def record(self, task_id: str, event: str, *, state: str = "running",
               stage: str = "execution", message: str = "",
               completed_units: int | None = None, total_units: int | None = None,
               evidence_ref: str | None = None, timestamp: str | None = None,
               priority: int | None = None, parent_task_id: str | None = None,
               decision_required: bool | None = None) -> ProgressEvent:
        return self._record(
            task_id, event, state=state, stage=stage, message=message,
            completed_units=completed_units, total_units=total_units,
            evidence_ref=evidence_ref, timestamp=timestamp, priority=priority,
            parent_task_id=parent_task_id, decision_required=decision_required,
            allow_terminal_correction=False,
        )

    def record_interrupt(self, task_id: str, *, priority: int, message: str,
                         parent_task_id: str | None = None,
                         decision_required: bool = False,
                         timestamp: str | None = None) -> ProgressEvent:
        """Durably enqueue an interruption without mutating the interrupted task."""
        if priority is not None and (isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100):
            raise ValueError("priority must be an integer from 0 to 100")
        if parent_task_id is not None and (not isinstance(parent_task_id, str) or not parent_task_id.strip()):
            raise ValueError("parent_task_id must be a non-empty string when supplied")
        if decision_required is not None and not isinstance(decision_required, bool):
            raise ValueError("decision_required must be a boolean")
        return self.record(
            task_id, "interrupt_received", state="blocked" if decision_required else "queued",
            stage="priority-stack", message=message, timestamp=timestamp,
            priority=priority, parent_task_id=parent_task_id,
            decision_required=decision_required,
        )

    def record_reconciliation(
        self, task_id: str, *, state: str, stage: str, message: str,
        evidence_ref: str | None = None, expected_sequence: int = 0,
    ) -> ProgressEvent:
        """Append an explicit supervisor correction when durable stores disagree.

        This is the only path allowed to supersede a terminal journal projection.
        It never deletes history; the latest event records the corrected state and
        why the prior projection was not trusted.
        """
        return self._record(
            task_id, "startup_reconciliation", state=state, stage=stage,
            message=message, evidence_ref=evidence_ref,
            expected_sequence=expected_sequence,
            allow_terminal_correction=True,
        )

    def _record(self, task_id: str, event: str, *, state: str = "running",
                stage: str = "execution", message: str = "",
                completed_units: int | None = None, total_units: int | None = None,
                evidence_ref: str | None = None, timestamp: str | None = None,
                priority: int | None = None, parent_task_id: str | None = None,
                decision_required: bool | None = None, expected_sequence: int | None = None,
                allow_terminal_correction: bool = False) -> ProgressEvent:
        if not task_id or not task_id.strip():
            raise ValueError("task_id must be non-empty")
        if state not in STATES:
            raise ValueError(f"unsupported state: {state}")
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100:
            raise ValueError("priority must be an integer from 0 to 100")
        if parent_task_id is not None and (not isinstance(parent_task_id, str) or not parent_task_id.strip()):
            raise ValueError("parent_task_id must be a non-empty string when supplied")
        if not isinstance(decision_required, bool):
            raise ValueError("decision_required must be a boolean")
        if completed_units is not None and completed_units < 0:
            raise ValueError("completed_units cannot be negative")
        if total_units is not None and total_units < 0:
            raise ValueError("total_units cannot be negative")
        if completed_units is not None and total_units is not None and completed_units > total_units:
            raise ValueError("completed_units cannot exceed total_units")
        if state == "completed" and completed_units is not None and total_units is not None and completed_units != total_units:
            raise ValueError("completed task must account for all declared units")
        lock_deadline = time.monotonic() + self.lock_timeout_seconds
        if not self._lock.acquire(timeout=self.lock_timeout_seconds):
            raise TimeoutError("progress journal lock acquisition timed out (in-process contention)")
        try:
            remaining = lock_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("progress journal lock acquisition timed out")
            with _journal_file_lock(self.path, timeout_seconds=remaining):
                task_rows = [r for r in self._events() if r["task_id"] == task_id]
                if expected_sequence is not None:
                    actual_sequence = int(task_rows[-1]["sequence"]) if task_rows else 0
                    if actual_sequence != expected_sequence:
                        raise RuntimeError("progress journal changed during reconciliation; retry")
                if task_rows and task_rows[-1]["state"] in TERMINAL and not allow_terminal_correction:
                    raise ValueError("cannot append progress to a terminal task")
                previous = task_rows[-1] if task_rows else {}
                effective_priority = previous.get("priority", 0) if priority is None else priority
                effective_parent = previous.get("parent_task_id") if parent_task_id is None else parent_task_id
                effective_decision = previous.get("decision_required", False) if decision_required is None else decision_required
                row = ProgressEvent(
                    sequence=task_rows[-1]["sequence"] + 1 if task_rows else 1,
                    task_id=task_id, event=_safe(event, 80), state=state,
                    stage=_safe(stage, 120), message=_safe(message),
                    timestamp=timestamp or _now(), completed_units=completed_units,
                    total_units=total_units, evidence_ref=_safe(evidence_ref, 1000) if evidence_ref else None,
                    priority=effective_priority, parent_task_id=_safe(effective_parent, 200) if effective_parent else None,
                    decision_required=effective_decision,
                )
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(asdict(row), sort_keys=True, separators=(",", ":")) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                return row

        finally:
            self._lock.release()

    def record_provider_event(self, payload: Mapping[str, Any]) -> ProgressEvent:
        """Project allowlisted provider lifecycle metadata into canonical task progress.

        Never persist provider prompts, responses, exception messages, or arbitrary
        callback fields. The task ID must be injected by the trusted host.
        """
        if not isinstance(payload, Mapping):
            raise TypeError("provider progress payload must be a mapping")
        task_id = payload.get("task_id")
        event = payload.get("event")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("provider progress requires a supervisor-issued task_id")
        if not isinstance(event, str) or event not in {
            "provider_call_started", "provider_call_timed_out", "provider_call_blocked",
            "provider_attempt_started", "provider_call_failed", "provider_result_ready",
        }:
            raise ValueError("unsupported provider progress event")
        provider = payload.get("provider", "provider")
        if not isinstance(provider, str) or not provider.strip():
            provider = "provider"
        stage = f"provider:{provider}"
        details = {
            "provider_call_started": ("running", "Provider call started with a bounded wait."),
            "provider_attempt_started": ("running", "Provider attempt started."),
            "provider_call_timed_out": ("blocked", "Provider wait timed out; late completion is possible. Reconcile before retry."),
            "provider_call_blocked": ("blocked", "Provider call could not start because bounded worker capacity is exhausted."),
            "provider_call_failed": ("recovering", "Provider attempt failed; supervisor is evaluating safe recovery."),
            "provider_result_ready": ("running", "Provider returned a result; validation and durable checkpointing remain."),
        }
        state, message = details[event]
        if event == "provider_call_failed":
            error_type = payload.get("error_type")
            if isinstance(error_type, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error_type):
                message = f"Provider attempt failed ({error_type}); supervisor is evaluating safe recovery."
        evidence_ref = payload.get("evidence_ref")
        if not isinstance(evidence_ref, str) or not evidence_ref.strip():
            evidence_ref = None
        return self.record(
            task_id, event, state=state, stage=stage, message=message,
            evidence_ref=evidence_ref,
        )

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
            "event_count": len(rows), "priority": latest.get("priority", 0),
            "parent_task_id": latest.get("parent_task_id"),
            "decision_required": latest.get("decision_required", False),
            "next_action": (
                "obtain authorized human decision before this task can proceed"
                if latest.get("decision_required", False) else
                "inspect worker health and deadline; recover or fail visibly" if stale else None
            ),
        }

    def list_snapshots(self, *, now: datetime | None = None, stale_after_seconds: int = 60) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(r["task_id"] for r in self._events()))
        return [self.snapshot(task_id, now=now, stale_after_seconds=stale_after_seconds) for task_id in ids]

    def priority_stack(self, *, now: datetime | None = None, stale_after_seconds: int = 60) -> list[dict[str, Any]]:
        """Return all non-terminal tasks in stable priority/FIFO order."""
        pending = [item for item in self.list_snapshots(now=now, stale_after_seconds=stale_after_seconds)
                   if item.get("reported_state", item.get("state")) not in TERMINAL]
        return sorted(pending, key=lambda item: (-item.get("priority", 0), item.get("last_update", ""), item["task_id"]))

    def next_ready(self, *, now: datetime | None = None, stale_after_seconds: int = 60) -> dict[str, Any] | None:
        """Select the highest-priority ready item without bypassing human gates."""
        for item in self.priority_stack(now=now, stale_after_seconds=stale_after_seconds):
            state = item.get("reported_state", item.get("state"))
            if state in {"queued", "recovering"} and not item.get("decision_required", False):
                return item
        return None
