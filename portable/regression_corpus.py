"""Governed regression corpus grown from verified EngineeringEpisodes.

The corpus is durable, deduplicated and lifecycle-managed:
candidate -> active -> retired/superseded.
Episodes may propose cases, but only independent evidence can activate them.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

from .persistent_memory import PersistentMemory


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class RegressionCase:
    case_id: str
    task_family: str
    description: str
    expected_success: bool
    expected_verification: bool
    risk: str = "medium"
    status: str = "candidate"
    fingerprint: str = ""
    source_episode: str = ""
    evidence_ids: tuple[str, ...] = ()
    validation_passes: int = 0
    validation_failures: int = 0
    consecutive_passes: int = 0
    created_at: str = ""
    updated_at: str = ""


class RegressionCorpus:
    """Persistent runtime corpus with evidence-gated promotion and retirement."""

    STATUSES = {"candidate", "active", "retired", "superseded"}
    PROMOTION_PASSES = 2

    def __init__(self, memory: PersistentMemory, project: str) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        if not project.strip():
            raise ValueError("project is required")
        self.memory = memory
        self.project = project.strip()
        with self.memory._lock, self.memory._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS regression_cases(
                project TEXT NOT NULL, case_id TEXT NOT NULL, task_family TEXT NOT NULL,
                description TEXT NOT NULL, expected_success INTEGER NOT NULL,
                expected_verification INTEGER NOT NULL, risk TEXT NOT NULL,
                status TEXT NOT NULL, fingerprint TEXT NOT NULL, source_episode TEXT NOT NULL,
                evidence_ids TEXT NOT NULL, validation_passes INTEGER NOT NULL,
                validation_failures INTEGER NOT NULL, consecutive_passes INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                superseded_by TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(project,case_id))""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_regression_fingerprint
                ON regression_cases(project,fingerprint)""")
            db.execute("""CREATE TABLE IF NOT EXISTS regression_validations(
                project TEXT NOT NULL, case_id TEXT NOT NULL, validation_digest TEXT NOT NULL,
                passed INTEGER NOT NULL, independent INTEGER NOT NULL, evidence_ids TEXT NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(project,case_id,validation_digest))""")

    @staticmethod
    def episode_fingerprint(*, task_family: str, task_id: str = "", intent_digest: str,
                            failure_class: str = "", dont_rules: Iterable[str] = ()) -> str:
        return _digest({
            "task_family": task_family.strip(),
            "intent_digest": intent_digest.strip(), "failure_class": failure_class.strip(),
            "dont_rules": sorted(set(str(x).strip() for x in dont_rules if str(x).strip())),
        })[:32]

    def ingest_episode(self, episode: object, *, task_family: str | None = None) -> RegressionCase:
        phase = str(getattr(getattr(episode, "phase", None), "value", getattr(episode, "phase", "")))
        if phase not in {"completed", "failed"}:
            raise ValueError("only completed or failed episodes can create regression cases")
        evidence = tuple(sorted(set(str(x).strip() for x in getattr(episode, "evidence_ids", ()) if str(x).strip())))
        if not evidence:
            raise ValueError("regression case creation requires episode evidence")
        family = (task_family or getattr(episode, "capability", "") or "engineering").strip()
        failure_class = str(getattr(episode, "failure_class", "") or "")
        rules = tuple(getattr(episode, "dont_rules", ()) or ())
        fingerprint = self.episode_fingerprint(
            task_family=family,
            task_id=str(getattr(episode, "task_id", "")),
            intent_digest=str(getattr(episode, "intent_digest", "")),
            failure_class=failure_class,
            dont_rules=rules,
        )
        episode_id = str(getattr(episode, "episode_id", "")).strip()
        if not episode_id:
            raise ValueError("episode_id is required")
        case_id = f"episode-regression:{episode_id}"
        description = (
            f"Prevent recurrence of verified failure '{failure_class}'"
            if phase == "failed" and failure_class
            else f"Replay verified engineering outcome for task '{getattr(episode, 'task_id', '')}'"
        )
        risk = "high" if phase == "failed" else "medium"
        now = _utc()
        with self.memory._lock, self.memory._connect() as db:
            existing = db.execute(
                "SELECT case_id,source_episode,evidence_ids,validation_passes,validation_failures,consecutive_passes,status,created_at,description,risk FROM regression_cases WHERE project=? AND fingerprint=?",
                (self.project, fingerprint),
            ).fetchone()
            if existing:
                merged_evidence = tuple(sorted(set(json.loads(existing[2])) | set(evidence)))
                status = existing[6] if existing[6] in {"candidate", "active"} else "candidate"
                db.execute(
                    """UPDATE regression_cases SET source_episode=?,evidence_ids=?,
                       status=?,updated_at=?,description=?,risk=? WHERE project=? AND fingerprint=?""",
                    (existing[1] + "," + episode_id if episode_id not in existing[1].split(",") else existing[1],
                     json.dumps(merged_evidence), status, now, description if phase == "failed" else existing[8],
                     "high" if phase == "failed" else existing[9], self.project, fingerprint),
                )
                return self.get_by_fingerprint(fingerprint)
            db.execute(
                """INSERT INTO regression_cases VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (self.project, case_id, family, description, 1, 1, risk, "candidate",
                 fingerprint, episode_id, json.dumps(evidence), 0, 0, 0, now, now, ""),
            )
        return self.get(case_id)

    def record_validation(self, case_id: str, *, passed: bool,
                          evidence_ids: Iterable[str], independent: bool = True) -> RegressionCase:
        evidence = tuple(sorted(set(str(x).strip() for x in evidence_ids if str(x).strip())))
        if not evidence:
            raise ValueError("regression validation requires evidence")
        if not independent:
            raise ValueError("regression promotion requires independent validation")
        digest = _digest({"case_id": case_id, "passed": passed, "evidence": evidence, "independent": independent})[:32]
        with self.memory._lock, self.memory._connect() as db:
            row = db.execute(
                "SELECT status,validation_passes,validation_failures,consecutive_passes,evidence_ids FROM regression_cases WHERE project=? AND case_id=?",
                (self.project, case_id),
            ).fetchone()
            if row is None:
                raise KeyError(case_id)
            inserted = db.execute(
                "INSERT OR IGNORE INTO regression_validations VALUES(?,?,?,?,?,?,?)",
                (self.project, case_id, digest, int(passed), 1, json.dumps(evidence), _utc()),
            ).rowcount
            if not inserted:
                return self.get(case_id)
            if passed:
                passes, failures, consecutive = row[1] + 1, row[2], row[3] + 1
                status = "active" if consecutive >= self.PROMOTION_PASSES else row[0]
            else:
                passes, failures, consecutive = row[1], row[2] + 1, 0
                status = "candidate" if row[0] == "active" else row[0]
            merged = tuple(sorted(set(json.loads(row[4])) | set(evidence)))
            db.execute(
                """UPDATE regression_cases SET status=?,validation_passes=?,validation_failures=?,
                   consecutive_passes=?,evidence_ids=?,updated_at=? WHERE project=? AND case_id=?""",
                (status, passes, failures, consecutive, json.dumps(merged), _utc(), self.project, case_id),
            )
        return self.get(case_id)

    def retire(self, case_id: str, *, replacement_case_id: str,
               evidence_ids: Iterable[str]) -> RegressionCase:
        evidence = tuple(sorted(set(str(x).strip() for x in evidence_ids if str(x).strip())))
        if not evidence:
            raise ValueError("retirement requires evidence")
        with self.memory._lock, self.memory._connect() as db:
            old = db.execute("SELECT status FROM regression_cases WHERE project=? AND case_id=?", (self.project, case_id)).fetchone()
            new = db.execute("SELECT status FROM regression_cases WHERE project=? AND case_id=?", (self.project, replacement_case_id)).fetchone()
            if old is None or new is None:
                raise KeyError("case or replacement case not found")
            if new[0] != "active":
                raise ValueError("replacement must be active before retirement")
            db.execute(
                "UPDATE regression_cases SET status='superseded',superseded_by=?,updated_at=? WHERE project=? AND case_id=? AND status IN ('candidate','active')",
                (replacement_case_id, _utc(), self.project, case_id),
            )
        return self.get(case_id)

    def get(self, case_id: str) -> RegressionCase:
        with self.memory._lock, self.memory._connect() as db:
            row = db.execute(
                "SELECT case_id,task_family,description,expected_success,expected_verification,risk,status,fingerprint,source_episode,evidence_ids,validation_passes,validation_failures,consecutive_passes,created_at,updated_at FROM regression_cases WHERE project=? AND case_id=?",
                (self.project, case_id),
            ).fetchone()
        if row is None:
            raise KeyError(case_id)
        return RegressionCase(
            row[0], row[1], row[2], bool(row[3]), bool(row[4]), row[5], row[6], row[7], row[8],
            tuple(json.loads(row[9])), int(row[10]), int(row[11]), int(row[12]), row[13], row[14],
        )

    def get_by_fingerprint(self, fingerprint: str) -> RegressionCase:
        with self.memory._lock, self.memory._connect() as db:
            row = db.execute("SELECT case_id FROM regression_cases WHERE project=? AND fingerprint=?", (self.project, fingerprint)).fetchone()
        if row is None:
            raise KeyError(fingerprint)
        return self.get(row[0])

    def active(self, *, task_family: str | None = None, limit: int = 100) -> tuple[RegressionCase, ...]:
        with self.memory._lock, self.memory._connect() as db:
            if task_family:
                rows = db.execute("SELECT case_id FROM regression_cases WHERE project=? AND task_family=? AND status='active' ORDER BY case_id LIMIT ?", (self.project, task_family, limit)).fetchall()
            else:
                rows = db.execute("SELECT case_id FROM regression_cases WHERE project=? AND status='active' ORDER BY case_id LIMIT ?", (self.project, limit)).fetchall()
        return tuple(self.get(row[0]) for row in rows)

    def export_jsonl(self, *, include_candidate: bool = False) -> str:
        statuses = ("active", "candidate") if include_candidate else ("active",)
        with self.memory._lock, self.memory._connect() as db:
            placeholders = ",".join("?" for _ in statuses)
            rows = db.execute(
                f"SELECT case_id,task_family,description,expected_success,expected_verification,risk,status,evidence_ids FROM regression_cases WHERE project=? AND status IN ({placeholders}) ORDER BY case_id",
                (self.project, *statuses),
            ).fetchall()
        return "".join(
            json.dumps({
                "case_id": row[0], "task_class": row[1], "description": row[2],
                "expected_success": bool(row[3]), "expected_verification": bool(row[4]),
                "risk": row[5], "status": row[6], "evidence_ids": json.loads(row[7]),
            }, sort_keys=True) + "\n" for row in rows
        )


__all__ = ["RegressionCase", "RegressionCorpus"]
