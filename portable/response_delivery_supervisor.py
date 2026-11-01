"""Supervisor-owned lifecycle for receiver-acknowledged HTTP response delivery."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .runtime_recovery import RuntimeExecutionGuard
from .progress_journal import ProgressJournal
from .response_delivery_metrics import aggregate_delivery_metrics
from .execution_controller import (
    ExecutionController,
    LeaseLostError,
    TaskCancellationRequested,
)


_HANDLER = "portable.response_delivery.await_receiver"
MAX_ACTIVE_DELIVERY_WORKERS = 8
WORKER_SLOT_LEASE_SECONDS = 30
WORKER_SLOT_HEARTBEAT_SECONDS = 5
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
        self._budget_lock = threading.RLock()
        self._execution_guards: dict[str, RuntimeExecutionGuard] = {}
        with sqlite3.connect(self.registry_path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS deliveries ("
                "response_id TEXT NOT NULL, delivery_id TEXT NOT NULL, task_id TEXT NOT NULL UNIQUE, "
                "response_sha256 TEXT NOT NULL, artifact_sha256 TEXT, budget_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "PRIMARY KEY(response_id, delivery_id))"
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(deliveries)")}
            if "budget_id" not in columns:
                db.execute("ALTER TABLE deliveries ADD COLUMN budget_id TEXT")
            if "artifact_sha256" not in columns:
                db.execute("ALTER TABLE deliveries ADD COLUMN artifact_sha256 TEXT")
            db.execute(
                "CREATE TABLE IF NOT EXISTS delivery_budgets ("
                "budget_id TEXT PRIMARY KEY, snapshot_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS delivery_worker_slots ("
                "task_id TEXT PRIMARY KEY, owner_token TEXT NOT NULL, lease_until TEXT NOT NULL, "
                "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_delivery_worker_slots_lease "
                "ON delivery_worker_slots(lease_until)"
            )
            legacy = db.execute(
                "SELECT task_id FROM deliveries WHERE budget_id IS NULL OR budget_id=''"
            ).fetchall()
            for (legacy_task_id,) in legacy:
                legacy_budget_id = f"delivery:{legacy_task_id}"
                db.execute(
                    "INSERT OR IGNORE INTO delivery_budgets(budget_id,snapshot_json) VALUES(?,?)",
                    (legacy_budget_id, json.dumps(RuntimeExecutionGuard().snapshot(), sort_keys=True, separators=(",", ":"))),
                )
                db.execute(
                    "UPDATE deliveries SET budget_id=? WHERE task_id=?",
                    (legacy_budget_id, legacy_task_id),
                )
        try:
            self.controller._load_handler(_HANDLER)
        except KeyError:
            self.controller.register_handler(
                _HANDLER, await_receiver_acknowledgement, execution_class="read_only",
            )

    def create(
        self, response_id: str, delivery_id: str, digest: str, *, deadline_seconds: float = 600.0,
        execution_guard: RuntimeExecutionGuard | None = None, budget_id: str | None = None,
        artifact_digest: str | None = None,
    ) -> str:
        if response_id != digest or len(digest) != 64:
            raise ValueError("response identity must equal its SHA-256 digest")
        if execution_guard is not None and not isinstance(execution_guard, RuntimeExecutionGuard):
            raise TypeError("execution_guard must be a RuntimeExecutionGuard")
        if budget_id is not None and (not isinstance(budget_id, str) or not budget_id.strip()):
            raise ValueError("budget_id must be a non-empty string")
        if artifact_digest is not None and (
            not isinstance(artifact_digest, str) or len(artifact_digest) != 64
            or any(ch not in "0123456789abcdef" for ch in artifact_digest)
        ):
            raise ValueError("artifact_digest must be a lowercase SHA-256 digest")
        with sqlite3.connect(self.registry_path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT task_id,response_sha256,artifact_sha256,budget_id FROM deliveries WHERE response_id=? AND delivery_id=?",
                (response_id, delivery_id),
            ).fetchone()
            if row:
                if row[1] != digest:
                    raise ValueError("delivery identity is already bound to a different response digest")
                if artifact_digest is not None and row[2] not in (None, artifact_digest):
                    raise ValueError("delivery identity is already bound to a different artifact digest")
                if budget_id is not None and row[3] not in (None, budget_id):
                    raise ValueError("delivery identity is already bound to a different crew budget")
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
            resolved_budget_id = budget_id or f"delivery:{task.id}"
            snapshot = execution_guard.snapshot() if execution_guard is not None else RuntimeExecutionGuard().snapshot()
            existing_budget = db.execute(
                "SELECT snapshot_json FROM delivery_budgets WHERE budget_id=?", (resolved_budget_id,),
            ).fetchone()
            if existing_budget is None:
                db.execute(
                    "INSERT INTO delivery_budgets(budget_id,snapshot_json) VALUES(?,?)",
                    (resolved_budget_id, json.dumps(snapshot, sort_keys=True, separators=(",", ":"))),
                )
            elif execution_guard is not None:
                persisted = json.loads(existing_budget[0])
                old_charges = persisted.get("delivery_charges", {})
                new_charges = snapshot.get("delivery_charges", {})
                if (
                    persisted.get("max_delivery_bytes") != snapshot.get("max_delivery_bytes")
                    or any(new_charges.get(key) != value for key, value in old_charges.items())
                ):
                    raise ValueError("shared crew budget does not match its durable checkpoint")
                db.execute(
                    "UPDATE delivery_budgets SET snapshot_json=?,updated_at=CURRENT_TIMESTAMP WHERE budget_id=?",
                    (json.dumps(snapshot, sort_keys=True, separators=(",", ":")), resolved_budget_id),
                )
            db.execute(
                "INSERT INTO deliveries(response_id,delivery_id,task_id,response_sha256,artifact_sha256,budget_id) VALUES(?,?,?,?,?,?)",
                (response_id, delivery_id, task.id, digest, artifact_digest or digest, resolved_budget_id),
            )
            if execution_guard is not None:
                self._execution_guards[resolved_budget_id] = execution_guard
            return task.id

    def consume_delivery_bytes(self, task_id: str, amount: int, *, operation_key: str) -> bool:
        """Charge a response chunk to the durable shared crew budget before sending it."""
        with self._budget_lock, sqlite3.connect(self.registry_path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT budget_id FROM deliveries WHERE task_id=?", (task_id,),
            ).fetchone()
            if row is None or not row[0]:
                raise RuntimeError("delivery task has no durable budget binding")
            budget_id = str(row[0])
            budget = db.execute(
                "SELECT snapshot_json FROM delivery_budgets WHERE budget_id=?", (budget_id,),
            ).fetchone()
            if budget is None:
                raise RuntimeError("shared delivery budget checkpoint is unavailable")
            guard = self._execution_guards.get(budget_id)
            persisted = json.loads(budget[0])
            if guard is None:
                guard = RuntimeExecutionGuard.resume(persisted)
                self._execution_guards[budget_id] = guard
            else:
                live_snapshot = guard.snapshot()
                persisted_charges = persisted.get("delivery_charges", {})
                live_charges = live_snapshot.get("delivery_charges", {})
                if (
                    live_snapshot.get("max_delivery_bytes") != persisted.get("max_delivery_bytes")
                    or any(live_charges.get(key) != value for key, value in persisted_charges.items())
                ):
                    raise RuntimeError("in-memory crew budget diverged from its durable checkpoint")
                if live_charges != persisted_charges:
                    db.execute(
                        "UPDATE delivery_budgets SET snapshot_json=?,updated_at=CURRENT_TIMESTAMP WHERE budget_id=?",
                        (json.dumps(live_snapshot, sort_keys=True, separators=(",", ":")), budget_id),
                    )
            previous_charge = guard.snapshot().get("delivery_charges", {}).get(operation_key)
            if previous_charge is not None:
                if previous_charge != amount:
                    raise ValueError("delivery operation key was reused with a different byte charge")
                return True
            charged = guard.consume_delivery_bytes(amount, operation_key=operation_key)
            if charged:
                db.execute(
                    "UPDATE delivery_budgets SET snapshot_json=?,updated_at=CURRENT_TIMESTAMP WHERE budget_id=?",
                    (json.dumps(guard.snapshot(), sort_keys=True, separators=(",", ":")), budget_id),
                )
            return charged

    def delivery_budget_snapshot(self, task_id: str) -> dict[str, Any]:
        with sqlite3.connect(self.registry_path) as db:
            row = db.execute(
                "SELECT b.snapshot_json FROM deliveries d JOIN delivery_budgets b ON b.budget_id=d.budget_id WHERE d.task_id=?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise FileNotFoundError("delivery budget checkpoint is unavailable")
        return json.loads(row[0])

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
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def _acquire_worker_slot(self, task_id: str) -> str | None:
        """Atomically reserve a host-wide worker slot across processes sharing the registry."""
        now = self._utc_now()
        now_text = now.isoformat(timespec="microseconds")
        lease_until = (now + timedelta(seconds=WORKER_SLOT_LEASE_SECONDS)).isoformat(timespec="microseconds")
        owner_token = f"{uuid.uuid4().hex}:{task_id}"
        with sqlite3.connect(self.registry_path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM delivery_worker_slots WHERE lease_until<=?", (now_text,))
            existing = db.execute(
                "SELECT owner_token FROM delivery_worker_slots WHERE task_id=?", (task_id,),
            ).fetchone()
            if existing is not None:
                return None
            active = int(db.execute("SELECT COUNT(*) FROM delivery_worker_slots").fetchone()[0])
            if active >= MAX_ACTIVE_DELIVERY_WORKERS:
                return None
            db.execute(
                "INSERT INTO delivery_worker_slots(task_id,owner_token,lease_until) VALUES(?,?,?)",
                (task_id, owner_token, lease_until),
            )
            return owner_token

    def _renew_worker_slot(self, task_id: str, owner_token: str) -> bool:
        now = self._utc_now()
        lease_until = (now + timedelta(seconds=WORKER_SLOT_LEASE_SECONDS)).isoformat(timespec="microseconds")
        with sqlite3.connect(self.registry_path, timeout=10) as db:
            cursor = db.execute(
                "UPDATE delivery_worker_slots SET lease_until=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE task_id=? AND owner_token=?",
                (lease_until, task_id, owner_token),
            )
            return cursor.rowcount == 1

    def _release_worker_slot(self, task_id: str, owner_token: str) -> None:
        with sqlite3.connect(self.registry_path, timeout=10) as db:
            db.execute(
                "DELETE FROM delivery_worker_slots WHERE task_id=? AND owner_token=?",
                (task_id, owner_token),
            )

    def shared_capacity_snapshot(self) -> dict[str, int | bool]:
        now_text = self._utc_now().isoformat(timespec="microseconds")
        with sqlite3.connect(self.registry_path, timeout=10) as db:
            db.execute("DELETE FROM delivery_worker_slots WHERE lease_until<=?", (now_text,))
            active = int(db.execute("SELECT COUNT(*) FROM delivery_worker_slots").fetchone()[0])
        return {
            "active_workers": active,
            "worker_limit": MAX_ACTIVE_DELIVERY_WORKERS,
            "available_workers": max(0, MAX_ACTIVE_DELIVERY_WORKERS - active),
            "saturated": active >= MAX_ACTIVE_DELIVERY_WORKERS,
        }

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
        shared_capacity = self.shared_capacity_snapshot()
        measurements = aggregate_delivery_metrics(journal_events)
        return {
            "capacity": capacity,
            "shared_capacity": shared_capacity,
            "tasks_by_state": states,
            "pending_tasks": states.get("pending", 0),
            "running_tasks": states.get("running", 0),
            "completed_tasks": states.get("completed", 0),
            "failed_tasks": states.get("failed", 0),
            "cancelled_tasks": states.get("cancelled", 0),
            "acknowledged_chunks": len(ack_rows),
            "unknown_outcomes": len(unknown_rows),
            "receiver_acknowledged_deliveries": len(completed_receipts),
            "measurements": measurements,
            "integrity": {
                "unknown_outcomes_block_replay": True,
                "completion_requires_receiver_ack": True,
                "atomic_protocols_are_not_chunked": True,
            },
            "source": "execution_controller_and_progress_journal",
        }

    def start(self, task_id: str, *, artifact_verified: bool = False) -> bool:
        """Start a pending delivery only when a bounded worker slot is available.

        Saturation leaves the durable task pending. A later host poll may retry;
        no task is dropped, marked failed, or implicitly completed.
        """
        global _SATURATION_COUNT
        task = self.controller.task(task_id)
        if task is None:
            raise FileNotFoundError("supervisor delivery task is unavailable")
        if type(artifact_verified) is not bool:
            raise TypeError("artifact_verified must be a boolean")
        if task.state == "running":
            # Reclaim only after both the lease and heartbeat are stale and the
            # host has reloaded and verified the immutable response artifact.
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
            if not artifact_verified:
                return False
            self.controller.recover_stale(
                task_id, reason="delivery host restarted; prior worker lease expired",
                stale_after_seconds=1,
            )
            task = self.controller.task(task_id)
            if task is not None and task.state == "pending":
                journal = ProgressJournal(self.journal_path)
                checkpoint = journal.snapshot(task_id)
                if checkpoint.get("state") != "running":
                    journal.record_reconciliation(
                        task_id, state="running", stage="response-delivery-recovery",
                        message="immutable artifact and acknowledged cursor reloaded; supervisor resumed delivery",
                        expected_sequence=int(checkpoint.get("sequence", 0)),
                    )
        if task is None or task.state != "pending":
            return False

        key = (str(self.controller.db_path), task_id)
        with _THREADS_LOCK:
            active = _THREADS.get(key)
        if active is not None and active.is_alive():
            # A reclaimed task may still have an old in-process worker unwinding
            # after its lease was revoked. Let that worker observe LeaseLostError
            # and exit before starting the replacement worker.
            active.join(timeout=2.0)
            if active.is_alive():
                return False
        with _THREADS_LOCK:
            active = _THREADS.get(key)
            if active is not None and active.is_alive():
                return False
            active_count = sum(1 for thread in _THREADS.values() if thread.is_alive())
            if active_count >= MAX_ACTIVE_DELIVERY_WORKERS:
                _SATURATION_COUNT += 1
                return False
            owner_token = self._acquire_worker_slot(task_id)
            if owner_token is None:
                _SATURATION_COUNT += 1
                return False
            thread = threading.Thread(
                target=self._run, args=(key, task_id, owner_token),
                name="auren-response-delivery-" + task_id[:8], daemon=True,
            )
            _THREADS[key] = thread
            try:
                thread.start()
            except BaseException:
                _THREADS.pop(key, None)
                self._release_worker_slot(task_id, owner_token)
                raise
        # Wait briefly for the durable controller to acquire the new lease.
        # Returning success while the task is still pending makes the HTTP host
        # appear runnable even though no worker has started.
        admission_deadline = time.monotonic() + 1.0
        while time.monotonic() < admission_deadline:
            current = self.controller.task(task_id)
            if current is None:
                return False
            if current.state == "running":
                if not thread.is_alive():
                    return False
                ProgressJournal(self.journal_path).record(
                    task_id, "response_delivery_worker_admitted", state="running",
                    stage="response-delivery",
                    message="supervisor worker acquired the durable delivery lease",
                )
                return True
            if current.state != "pending" or not thread.is_alive():
                return False
            time.sleep(0.01)
        return False

    def _run(self, key: tuple[str, str], task_id: str, owner_token: str) -> None:
        heartbeat_stop = threading.Event()
        heartbeat_lost = threading.Event()

        def renew_slot() -> None:
            while not heartbeat_stop.wait(WORKER_SLOT_HEARTBEAT_SECONDS):
                try:
                    if not self._renew_worker_slot(task_id, owner_token):
                        heartbeat_lost.set()
                        try:
                            self.controller.request_cancellation(
                                task_id, reason="response delivery lost its shared worker-slot lease",
                            )
                        except Exception:
                            pass
                        return
                except Exception:
                    # Do not silently assume the shared slot is still owned.
                    heartbeat_lost.set()
                    try:
                        self.controller.request_cancellation(
                            task_id, reason="response delivery could not renew its shared worker-slot lease",
                        )
                    except Exception:
                        pass
                    return

        heartbeat = threading.Thread(
            target=renew_slot, name="auren-delivery-slot-" + task_id[:8], daemon=True,
        )
        try:
            if not self._renew_worker_slot(task_id, owner_token):
                return
            heartbeat.start()
            if not heartbeat_lost.is_set():
                self.controller.run_once(task_id)
        except LeaseLostError:
            # Expected when a replacement supervisor has fenced this worker.
            # The new lease owner is responsible for continuing the task.
            pass
        finally:
            heartbeat_stop.set()
            if heartbeat.is_alive():
                heartbeat.join(timeout=1.0)
            try:
                self._release_worker_slot(task_id, owner_token)
            finally:
                with _THREADS_LOCK:
                    _THREADS.pop(key, None)

    def state(self, task_id: str) -> str:
        task = self.controller.task(task_id)
        return task.state if task is not None else "unknown"

    def cancel(self, task_id: str, *, reason: str = "receiver delivery cancelled") -> bool:
        return self.controller.request_cancellation(task_id, reason=reason)

    def shutdown(self, *, wait_seconds: float = 2.0) -> None:
        """Request cancellation of this host's active delivery tasks and join workers."""
        with sqlite3.connect(self.controller.db_path) as db:
            rows = db.execute(
                "SELECT id FROM tasks WHERE work_key LIKE 'dashboard-response:%' AND state IN ('pending','running')"
            ).fetchall()
        for (task_id,) in rows:
            try:
                self.controller.request_cancellation(str(task_id), reason="delivery host shutdown")
            except Exception:
                # Preserve the durable task state; failed cancellation remains
                # visible to restart reconciliation rather than being hidden.
                continue
        deadline = time.monotonic() + max(0.0, wait_seconds)
        key_prefix = str(self.controller.db_path)
        while time.monotonic() < deadline:
            with _THREADS_LOCK:
                workers = [
                    thread for (db_path, _task_id), thread in _THREADS.items()
                    if db_path == key_prefix and thread.is_alive()
                ]
            if not workers:
                break
            for worker in workers:
                worker.join(timeout=min(0.05, max(0.0, deadline - time.monotonic())))
