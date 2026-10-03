"""Append-only reasoning trace for AUREN cognitive decisions.

This records the compact chain from observation and hypotheses through action
and outcome. It is an audit surface, not an execution authority and never
executes or promotes a decision by itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any

from .persistent_memory import PersistentMemory


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("recorded_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("recorded_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class ReasoningRecord:
    record_id: str
    run_id: str
    stage: str
    observation_ids: tuple[str, ...] = ()
    hypothesis_ids: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    decision: str = ""
    action: str = ""
    outcome: str = ""
    evidence_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    recorded_at: str = ""

    def __post_init__(self) -> None:
        for name, value in (("record_id", self.record_id), ("run_id", self.run_id), ("stage", self.stage)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not self.recorded_at:
            object.__setattr__(self, "recorded_at", _utc())
        object.__setattr__(self, "recorded_at", _timestamp(self.recorded_at))
        for name in ("observation_ids", "hypothesis_ids", "alternatives", "evidence_ids"):
            value = getattr(self, name)
            if any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError(f"{name} must contain non-empty strings")


class ReasoningLedger:
    """Bounded append-only reasoning records on the canonical AUREN memory DB."""

    def __init__(self, memory: PersistentMemory, project: str, *, max_records: int = 100_000) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project is required")
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self.memory = memory
        self.project = project
        self.max_records = max_records
        with sqlite3.connect(self.memory.path, timeout=10) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS reasoning_ledger(
                project TEXT NOT NULL, record_id TEXT NOT NULL, run_id TEXT NOT NULL,
                stage TEXT NOT NULL, observation_ids TEXT NOT NULL, hypothesis_ids TEXT NOT NULL,
                alternatives TEXT NOT NULL, decision TEXT NOT NULL, action TEXT NOT NULL,
                outcome TEXT NOT NULL, evidence_ids TEXT NOT NULL, confidence REAL NOT NULL,
                recorded_at TEXT NOT NULL, PRIMARY KEY(project, record_id))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_reasoning_run ON reasoning_ledger(project, run_id, recorded_at, record_id)")

    def append(self, record: ReasoningRecord) -> ReasoningRecord:
        values = (self.project, record.record_id, record.run_id, record.stage,
                  *[json.dumps(list(getattr(record, name)), separators=(",", ":")) for name in
                    ("observation_ids", "hypothesis_ids", "alternatives")],
                  record.decision, record.action, record.outcome,
                  json.dumps(list(record.evidence_ids), separators=(",", ":")),
                  record.confidence, record.recorded_at)
        with sqlite3.connect(self.memory.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT run_id,stage,observation_ids,hypothesis_ids,alternatives,decision,action,outcome,evidence_ids,confidence,recorded_at FROM reasoning_ledger WHERE project=? AND record_id=?", (self.project, record.record_id)).fetchone()
            expected = values[2:]
            if existing:
                if existing != expected:
                    raise ValueError("record_id already exists with different content")
                return record
            count = db.execute("SELECT COUNT(*) FROM reasoning_ledger WHERE project=?", (self.project,)).fetchone()[0]
            if count >= self.max_records:
                raise ValueError("reasoning ledger budget exceeded")
            db.execute("INSERT INTO reasoning_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
        return record

    def run(self, run_id: str, *, limit: int = 100) -> tuple[ReasoningRecord, ...]:
        if not run_id.strip():
            raise ValueError("run_id is required")
        if limit < 1:
            return ()
        with sqlite3.connect(self.memory.path, timeout=10) as db:
            rows = db.execute("SELECT record_id,run_id,stage,observation_ids,hypothesis_ids,alternatives,decision,action,outcome,evidence_ids,confidence,recorded_at FROM reasoning_ledger WHERE project=? AND run_id=? ORDER BY recorded_at,record_id LIMIT ?", (self.project, run_id, limit)).fetchall()
        return tuple(self._record(row) for row in rows)

    @staticmethod
    def _record(row: tuple[Any, ...]) -> ReasoningRecord:
        return ReasoningRecord(row[0], row[1], row[2], tuple(json.loads(row[3])), tuple(json.loads(row[4])),
                               tuple(json.loads(row[5])), row[6], row[7], row[8], tuple(json.loads(row[9])), float(row[10]), row[11])

    def digest(self, run_id: str) -> str:
        records = self.run(run_id, limit=self.max_records)
        payload = [record.__dict__ for record in records]
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()[:16]


__all__ = ["ReasoningLedger", "ReasoningRecord"]
