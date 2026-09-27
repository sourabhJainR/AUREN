"""Durable remediation backlog shared across sessions and execution episodes.

The backlog is a small coordination layer over the existing PersistentMemory
store. It owns remediation state; learning, curriculum, and resource routers
remain owners of their respective durable observations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .persistent_memory import PersistentMemory


STATUSES = {"pending", "in_progress", "blocked", "resolved", "accepted", "deferred"}
SEVERITY_WEIGHT = {"blocker": 100, "critical": 90, "high": 70, "medium": 45, "low": 20}


def _clean(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value.strip()


@dataclass(frozen=True, slots=True)
class PersistentRemediationItem:
    finding_id: str
    project: str
    task_family: str
    capability: str
    hat: str
    severity: str
    title: str
    detail: str
    recommendation: str
    evidence_ids: tuple[str, ...]
    status: str
    priority: float
    attempts: int = 0
    first_seen: str = ""
    last_seen: str = ""
    last_session_id: str = ""
    last_episode_id: str = ""
    resolution_evidence_ids: tuple[str, ...] = ()
    curriculum_condition: str = "remediation"
    resource_key: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id, "project": self.project,
            "task_family": self.task_family, "capability": self.capability,
            "hat": self.hat, "severity": self.severity, "title": self.title,
            "detail": self.detail, "recommendation": self.recommendation,
            "evidence_ids": list(self.evidence_ids), "status": self.status,
            "priority": round(self.priority, 4), "attempts": self.attempts,
            "first_seen": self.first_seen, "last_seen": self.last_seen,
            "last_session_id": self.last_session_id, "last_episode_id": self.last_episode_id,
            "resolution_evidence_ids": list(self.resolution_evidence_ids),
            "curriculum_condition": self.curriculum_condition,
            "resource_key": self.resource_key,
        }


class PersistentRemediationBacklog:
    """Persistent finding ledger with deterministic reprioritization.

    It is safe to construct repeatedly: the SQLite table is created if absent,
    and finding IDs are upserted rather than recreated per process.
    """

    def __init__(self, memory: PersistentMemory, project: str) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        self.memory = memory
        self.project = _clean(project, "project")
        with self.memory._lock, self.memory._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS remediation_backlog(
                finding_id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                task_family TEXT NOT NULL,
                capability TEXT NOT NULL,
                hat TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                evidence_ids TEXT NOT NULL,
                status TEXT NOT NULL,
                priority REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                last_session_id TEXT NOT NULL DEFAULT '',
                last_episode_id TEXT NOT NULL DEFAULT '',
                resolution_evidence_ids TEXT NOT NULL DEFAULT '[]',
                curriculum_condition TEXT NOT NULL DEFAULT 'remediation',
                resource_key TEXT NOT NULL DEFAULT ''
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_remediation_pending ON remediation_backlog(project,status,priority)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_remediation_family ON remediation_backlog(project,task_family,capability,status)")

    @staticmethod
    def _priority(severity: str, attempts: int, first_seen: str, status: str) -> float:
        weight = SEVERITY_WEIGHT.get(str(severity).strip().lower(), 30)
        try:
            age_days = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(first_seen.replace("Z", "+00:00"))).total_seconds() / 86400.0)
        except (TypeError, ValueError):
            age_days = 0.0
        retry_pressure = min(25.0, attempts * 5.0)
        unresolved = 1.0 if status in {"pending", "in_progress", "blocked", "deferred"} else 0.0
        return weight + min(25.0, age_days) + retry_pressure + (10.0 if status == "blocked" else 0.0) if unresolved else 0.0

    def upsert(
        self,
        *,
        finding_id: str,
        task_family: str,
        capability: str,
        hat: str,
        severity: str,
        title: str,
        detail: str,
        recommendation: str,
        evidence_ids: Iterable[str] = (),
        status: str = "pending",
        session_id: str = "",
        episode_id: str = "",
        resolution_evidence_ids: Iterable[str] = (),
        curriculum_condition: str = "remediation",
        resource_key: str = "",
        attempts_increment: int = 0,
    ) -> PersistentRemediationItem:
        finding_id = _clean(finding_id, "finding_id")
        task_family = _clean(task_family, "task_family")
        capability = _clean(capability, "capability")
        hat = _clean(hat, "hat")
        severity = _clean(severity, "severity").lower()
        status = _clean(status, "status").lower()
        if status not in STATUSES:
            raise ValueError(f"unsupported remediation status: {status}")
        if attempts_increment < 0:
            raise ValueError("attempts_increment cannot be negative")
        evidence = tuple(dict.fromkeys(_clean(str(x), "evidence_id") for x in evidence_ids))
        resolution = tuple(dict.fromkeys(_clean(str(x), "resolution_evidence_id") for x in resolution_evidence_ids))
        now = datetime.now(timezone.utc).isoformat()
        with self.memory._lock, self.memory._connect() as db:
            row = db.execute("SELECT first_seen,attempts,status FROM remediation_backlog WHERE finding_id=?", (finding_id,)).fetchone()
            first_seen = str(row[0]) if row else now
            attempts = (int(row[1]) if row else 0) + attempts_increment
            priority = self._priority(severity, attempts, first_seen, status)
            db.execute(
                """INSERT INTO remediation_backlog
                (finding_id,project,task_family,capability,hat,severity,title,detail,recommendation,
                 evidence_ids,status,priority,attempts,first_seen,last_seen,last_session_id,last_episode_id,
                 resolution_evidence_ids,curriculum_condition,resource_key)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(finding_id) DO UPDATE SET
                 project=excluded.project,task_family=excluded.task_family,capability=excluded.capability,
                 hat=excluded.hat,severity=excluded.severity,title=excluded.title,detail=excluded.detail,
                 recommendation=excluded.recommendation,evidence_ids=excluded.evidence_ids,status=excluded.status,
                 priority=excluded.priority,attempts=excluded.attempts,last_seen=excluded.last_seen,
                 last_session_id=excluded.last_session_id,last_episode_id=excluded.last_episode_id,
                 resolution_evidence_ids=excluded.resolution_evidence_ids,
                 curriculum_condition=excluded.curriculum_condition,resource_key=excluded.resource_key""",
                (finding_id,self.project,task_family,capability,hat,severity,title,detail,recommendation,
                 json.dumps(evidence),status,priority,attempts,first_seen,now,session_id,episode_id,
                 json.dumps(resolution),curriculum_condition,resource_key),
            )
        return self.get(finding_id)  # type: ignore[return-value]

    def get(self, finding_id: str) -> PersistentRemediationItem | None:
        finding_id = _clean(finding_id, "finding_id")
        with self.memory._lock, self.memory._connect() as db:
            row = db.execute("SELECT * FROM remediation_backlog WHERE finding_id=? AND project=?", (finding_id,self.project)).fetchone()
        return self._row(row) if row else None

    def pending(self, *, limit: int = 50, task_family: str | None = None, capability: str | None = None) -> list[PersistentRemediationItem]:
        if limit < 1: raise ValueError("limit must be positive")
        query = "SELECT * FROM remediation_backlog WHERE project=? AND status IN ('pending','in_progress','blocked','deferred')"
        args: list[Any] = [self.project]
        if task_family: query += " AND task_family=?"; args.append(task_family)
        if capability: query += " AND capability=?"; args.append(capability)
        query += " ORDER BY priority DESC, first_seen ASC, finding_id ASC LIMIT ?"; args.append(limit)
        with self.memory._lock, self.memory._connect() as db:
            rows = db.execute(query, tuple(args)).fetchall()
        return [self._row(row) for row in rows]

    def set_status(self, finding_id: str, status: str, *, session_id: str = "", episode_id: str = "", evidence_ids: Iterable[str] = ()) -> PersistentRemediationItem:
        item = self.get(finding_id)
        if item is None: raise KeyError(f"unknown finding id: {finding_id}")
        status = _clean(status, "status").lower()
        if status not in STATUSES: raise ValueError(f"unsupported remediation status: {status}")
        return self.upsert(
            finding_id=item.finding_id, task_family=item.task_family, capability=item.capability,
            hat=item.hat, severity=item.severity, title=item.title, detail=item.detail,
            recommendation=item.recommendation, evidence_ids=item.evidence_ids, status=status,
            session_id=session_id, episode_id=episode_id,
            resolution_evidence_ids=evidence_ids or item.resolution_evidence_ids,
            curriculum_condition=item.curriculum_condition, resource_key=item.resource_key,
        )

    def reprioritize(self) -> list[PersistentRemediationItem]:
        with self.memory._lock, self.memory._connect() as db:
            rows = db.execute("SELECT finding_id,severity,attempts,first_seen,status FROM remediation_backlog WHERE project=?", (self.project,)).fetchall()
            for finding_id,severity,attempts,first_seen,status in rows:
                db.execute("UPDATE remediation_backlog SET priority=? WHERE finding_id=?",
                           (self._priority(str(severity),int(attempts),str(first_seen),str(status)),str(finding_id)))
        return self.pending(limit=10000)

    def resume_ids(self, *, limit: int = 50) -> tuple[str, ...]:
        """Return durable unresolved finding IDs for the next execution episode."""
        return tuple(item.finding_id for item in self.reprioritize()[:limit])

    def learning_constraints(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return [{
            "finding_id": x.finding_id, "problem": x.detail,
            "dont": f"Do not repeat the unresolved remediation approach for '{x.title}': {x.recommendation}",
            "evidence_ids": list(x.evidence_ids + x.resolution_evidence_ids),
            "confidence": 0.95 if x.severity.lower() in {"blocker","critical","high"} else 0.85,
            "source_project": x.project,
        } for x in self.pending(limit=limit) if x.evidence_ids or x.resolution_evidence_ids]

    def feed_learning(self, learning_transfer: Any, *, limit: int = 50) -> int:
        """Publish unresolved findings as evidence-backed failure constraints."""
        if not hasattr(learning_transfer, "record_failure"):
            raise TypeError("learning_transfer must expose record_failure")
        count = 0
        for item in self.learning_constraints(limit=limit):
            evidence = tuple(item["evidence_ids"])
            if evidence:
                learning_transfer.record_failure(problem=str(item["problem"]), dont=str(item["dont"]), evidence_ids=evidence, confidence=float(item["confidence"]))
                count += 1
        return count

    def feed_curriculum(self, curriculum: Any, *, limit: int = 50) -> int:
        """Publish unresolved findings as verified low-score curriculum observations."""
        if not hasattr(curriculum, "record_outcome"):
            raise TypeError("curriculum must expose record_outcome")
        count = 0
        for item in self.curriculum_observations(limit=limit):
            curriculum.record_outcome(str(item["capability"]), str(item["task_family"]), str(item["condition"]), score=0.0, verified=True)
            count += 1
        return count

    def curriculum_observations(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return [{
            "finding_id": x.finding_id, "capability": x.capability,
            "task_family": x.task_family, "condition": x.curriculum_condition,
            "priority": x.priority, "attempts": x.attempts, "verified": True,
        } for x in self.pending(limit=limit)]

    def resource_routes(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return [{
            "finding_id": x.finding_id, "routing_key": x.resource_key,
            "task_family": x.task_family, "capability": x.capability,
            "priority": x.priority, "attempts": x.attempts,
        } for x in self.pending(limit=limit) if x.resource_key]

    def _row(self, row: Any) -> PersistentRemediationItem:
        return PersistentRemediationItem(
            finding_id=str(row[0]), project=str(row[1]), task_family=str(row[2]),
            capability=str(row[3]), hat=str(row[4]), severity=str(row[5]),
            title=str(row[6]), detail=str(row[7]), recommendation=str(row[8]),
            evidence_ids=tuple(json.loads(row[9] or "[]")), status=str(row[10]),
            priority=float(row[11]), attempts=int(row[12]), first_seen=str(row[13]),
            last_seen=str(row[14]), last_session_id=str(row[15]), last_episode_id=str(row[16]),
            resolution_evidence_ids=tuple(json.loads(row[17] or "[]")),
            curriculum_condition=str(row[18]), resource_key=str(row[19]),
        )


__all__ = ["PersistentRemediationBacklog", "PersistentRemediationItem", "STATUSES"]
