"""Supervisor-owned lifecycle for receiver-acknowledged HTTP response delivery."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from .execution_controller import (
    ExecutionController,
    LeaseLostError,
    TaskCancellationRequested,
)


_HANDLER = "portable.response_delivery.await_receiver"
MAX_ACTIVE_DELIVERY_WORKERS = 8
_THREADS: dict[tuple[str, str], threading.Thread] = {}
_THREADS_LOCK = threading.Lock()
_SATURATION_COUNT = 0


def await_receiver_acknowledgement(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the supervisor parent nonterminal until a digest-bound receiver receipt exists."""
    journal_path = payload.get("journal_path")
    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        db_path = payload.get("controller_db_path")
        work_key = payload.get("work_key")
        if not isinstance(db_path, str) or not isinstance(work_key, str):
            raise ValueError("response delivery supervisor binding is incomplete")
        with sqlite3.connect(db_path) as db:
            row = db.execute("SELECT id FROM tasks WHERE work_key=?", (work_key,)).fetchone()
        task_id = str(row[0]) if row else None
    response_id = payload.get("response_id")
    digest = payload.get("response_sha256")
    if not all(isinstance(value, str) and value for value in (journal_path, task_id, response_id, digest)):
        raise ValueError("response delivery task envelope is incomplete")
    if response_id != digest or len(digest) != 64:
        raise ValueError("response delivery task digest binding is invalid")

    from .progress_journal import ProgressJournal

    journal = ProgressJournal(Path(journal_path))
    db_path = payload.get("controller_db_path")
    with sqlite3.connect(db_path) as db:
        lease_row = db.execute(
            "SELECT state,lease_generation,lease_token FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
    if lease_row is None or lease_row[0] != "running" or not lease_row[2]:
        raise LeaseLostError("response delivery worker has no active supervisor lease")
    owned_generation, owned_token = int(lease_row[1]), str(lease_row[2])
    cancel_event = payload.get("auren_cancel_event")
    while True:
        with sqlite3.connect(db_path) as db:
            current = db.execute(
                "SELECT state,lease_generation,lease_token FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
        if current is None or current[0] != "running" or int(current[1]) != owned_generation or current[2] != owned_token:
            raise LeaseLostError("response delivery supervisor lease was replaced; stale worker stopped")
        if cancel_event is not None and cancel_event.is_set():
            raise TaskCancellationRequested("response delivery cancelled by supervisor")
        rows = [row for row in journal._events() if row.get("task_id") == task_id]
        for row in reversed(rows):
            if row.get("event") == "response_delivery_receiver_acknowledged":
                if row.get("state") != "running" or row.get("evidence_ref") != "sha256:" + digest:
                    raise RuntimeError("receiver acknowledgement evidence does not match the response artifact")
                return {"success": True, "detail": "receiver acknowledgement verified", "evidence": ["sha256:" + digest]}
            if row.get("state") in {"failed", "cancelled", "blocked_reconciliation"}:
                raise RuntimeError("response delivery became terminal before receiver acknowledgement")
        time.sleep(0.05)


class ResponseDeliverySupervisor:
    """Binds generated delivery identities to durable ExecutionController tasks."""

    def __init__(self, controller: ExecutionController, journal_path: str | Path):
        self.controller = controller
        self.journal_path = Path(journal_path).expanduser().resolve()
        self.registry_path = controller.db_path.parent / "response_delivery_registry.sqlite3"
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.registry_path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS deliveries ("
                "response_id TEXT NOT NULL, delivery_id TEXT NOT NULL, task_id TEXT NOT NULL UNIQUE, "
                "response_sha256 TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "PRIMARY KEY(response_id, delivery_id))"
            )
        try:
            self.controller._load_handler(_HANDLER)
        except KeyError:
            self.controller.register_handler(
                _HANDLER, await_receiver_acknowledgement, execution_class="read_only",
            )

    def create(self, response_id: str, delivery_id: str, digest: str, *, deadline_seconds: float = 600.0) -> str:
        if response_id != digest or len(digest) != 64:
            raise ValueError("response identity must equal its SHA-256 digest")
        with sqlite3.connect(self.registry_path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT task_id,response_sha256 FROM deliveries WHERE response_id=? AND delivery_id=?",
                (response_id, delivery_id),
            ).fetchone()
            if row:
                if row[1] != digest:
                    raise ValueError("delivery identity is already bound to a different response digest")
                task_id = str(row[0])
                task = self.controller.task(task_id)
                if task is None:
                    raise RuntimeError("delivery registry references a missing supervisor task")
                return task_id
            work_key = f"dashboard-response:{response_id}:{delivery_id}"
            task = self.controller.submit(
                _HANDLER,
                {
                    "task_id": None,
                    "controller_db_path": str(self.controller.db_path),
                    "work_key": work_key,
                    "response_id": response_id,
                    "delivery_id": delivery_id,
                    "response_sha256": digest,
                    "journal_path": str(self.journal_path),
                },
                goal="deliver immutable response and await receiver acknowledgement",
                work_key=work_key,
                deadline_seconds=deadline_seconds,
            )
            db.execute(
                "INSERT INTO deliveries(response_id,delivery_id,task_id,response_sha256) VALUES(?,?,?,?)",
                (response_id, delivery_id, task.id, digest),
            )
            return task.id

    def task_id(self, response_id: str, delivery_id: str) -> str:
        with sqlite3.connect(self.registry_path) as db:
            row = db.execute(
                "SELECT task_id,response_sha256 FROM deliveries WHERE response_id=? AND delivery_id=?",
                (response_id, delivery_id),
            ).fetchone()
        if row is None or row[1] != response_id:
            raise FileNotFoundError("supervisor delivery binding is unavailable")
        if self.controller.task(str(row[0])) is None:
            raise RuntimeError("supervisor delivery task is unavailable")
        return str(row[0])

    @staticmethod
    def capacity_snapshot() -> dict[str, int | bool]:
        """Return bounded worker capacity and in-process saturation telemetry."""
        with _THREADS_LOCK:
            active = sum(1 for thread in _THREADS.values() if thread.is_alive())
            return {
                "active_workers": active,
                "worker_limit": MAX_ACTIVE_DELIVERY_WORKERS,
                "available_workers": max(0, MAX_ACTIVE_DELIVERY_WORKERS - active),
                "saturated": active >= MAX_ACTIVE_DELIVERY_WORKERS,
                "saturation_events": _SATURATION_COUNT,
            }

    def metrics_snapshot(self) -> dict[str, Any]:
        """Expose delivery queue, integrity and lifecycle metrics from durable state."""
        states: dict[str, int] = {}
        with sqlite3.connect(self.controller.db_path) as db:
            rows = db.execute(
                "SELECT state,COUNT(*) FROM tasks WHERE work_key LIKE 'dashboard-response:%' GROUP BY state"
            ).fetchall()
            states = {str(state): int(count) for state, count in rows}
        events = ProgressJournal(self.journal_path)._events()
        delivery_events = [
            row for row in events
            if str(row.get("task_id", "")).strip()
            and str(row.get("stage", "")).startswith("response-delivery:")
        ]
        ack_rows = [row for row in delivery_events if row.get("event") == "response_chunk_acknowledged"]
        unknown_rows = [row for row in delivery_events if row.get("event") == "response_chunk_outcome_unknown"]
        completed_receipts = {
            row.get("task_id") for row in delivery_events
            if row.get("event") == "response_delivery_receiver_acknowledged"
        }
        capacity = self.capacity_snapshot()
        return {
            "capacity": capacity,
            "tasks_by_state": states,
            "pending_tasks": states.get("pending", 0),
            "running_tasks": states.get("running", 0),
            "completed_tasks": states.get("completed", 0),
            "failed_tasks": states.get("failed", 0),
            "cancelled_tasks": states.get("cancelled", 0),
            "acknowledged_chunks": len(ack_rows),
            "unknown_outcomes": len(unknown_rows),
            "receiver_acknowledged_deliveries": len(completed_receipts),
            "integrity": {
                "unknown_outcomes_block_replay": True,
                "completion_requires_receiver_ack": True,
                "atomic_protocols_are_not_chunked": True,
            },
            "source": "execution_controller_and_progress_journal",
        }

    def start(self, task_id: str) -> bool:
        """Start a pending delivery only when a bounded worker slot is available.

        Saturation leaves the durable task pending. A later host poll may retry;
        no task is dropped, marked failed, or implicitly completed.
        """
        global _SATURATION_COUNT
        task = self.controller.task(task_id)
        if task is None:
            raise FileNotFoundError("supervisor delivery task is unavailable")
        if task.state == "running":
            # Reclaim only after both the lease and heartbeat are stale.
            with sqlite3.connect(self.controller.db_path) as db:
                row = db.execute("SELECT lease_until FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row and row[0]:
                from datetime import datetime, timezone
                try:
                    lease = datetime.fromisoformat(str(row[0]))
                except ValueError:
                    return False
                if lease.tzinfo is None or lease > datetime.now(timezone.utc):
                    return False
            self.controller.recover_stale(
                task_id, reason="delivery host restarted; prior worker lease expired",
                stale_after_seconds=1,
            )
            task = self.controller.task(task_id)
        if task is None or task.state != "pending":
            return False

        key = (str(self.controller.db_path), task_id)
        with _THREADS_LOCK:
            active = _THREADS.get(key)
            if active is not None and active.is_alive():
                return True
            active_count = sum(1 for thread in _THREADS.values() if thread.is_alive())
            if active_count >= MAX_ACTIVE_DELIVERY_WORKERS:
                _SATURATION_COUNT += 1
                return False
            thread = threading.Thread(
                target=self._run, args=(key, task_id),
                name="auren-response-delivery-" + task_id[:8], daemon=True,
            )
            _THREADS[key] = thread
            thread.start()
            return True

    def _run(self, key: tuple[str, str], task_id: str) -> None:
        try:
            self.controller.run_once(task_id)
        finally:
            with _THREADS_LOCK:
                _THREADS.pop(key, None)

    def state(self, task_id: str) -> str:
        task = self.controller.task(task_id)
        return task.state if task is not None else "unknown"

    def cancel(self, task_id: str, *, reason: str = "receiver delivery cancelled") -> bool:
        return self.controller.request_cancellation(task_id, reason=reason)
