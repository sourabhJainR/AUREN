#!/usr/bin/env python3
"""Portable, versioned and concurrency-safe durable learning ledger.

The ledger stores observations, not transcripts.  Dreaming curates repeated
observations into reusable patterns after execution has finished.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 3
LEARNING_VERSION = "3.0"
CATEGORIES = {"command", "approach", "bug", "feature", "regression", "environment", "verification"}
OUTCOMES = {"worked", "failed", "partial", "not-applicable", "regressed"}
PROMOTIONS = {"candidate", "verified", "superseded", "rejected"}
MAX_FIELD = 8000


def _clean(value: Any, limit: int = MAX_FIELD) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 40] + " ...[guardrail-trimmed]"


def _logical_key(task: str, category: str, command: str | None, approach: str | None) -> str:
    value = "|".join((task, category, command or "", approach or "")).lower()
    return "lk-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _observation_fingerprint(task, category, outcome, command, approach, detail, refs, evidence) -> str:
    value = [task, category, outcome, command, approach, detail, refs, evidence]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _id(logical_key: str, revision: int, fingerprint: str) -> str:
    return "tm-" + hashlib.sha256(f"{logical_key}|{revision}|{fingerprint}".encode()).hexdigest()[:20]


@contextmanager
def _process_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _db_path(root: Path) -> Path:
    path = Path(root) / ".ai-harness" / "learning" / "task-memory.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect(root: Path) -> sqlite3.Connection:
    db = sqlite3.connect(_db_path(root), timeout=30, isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute(
        """CREATE TABLE IF NOT EXISTS observations (
            id TEXT PRIMARY KEY, recorded_at INTEGER NOT NULL, task TEXT NOT NULL,
            category TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL,
            command TEXT, approach TEXT, construct_refs TEXT NOT NULL,
            run_id TEXT, evidence_ids TEXT NOT NULL, status TEXT NOT NULL,
            promotion TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
            learning_version TEXT NOT NULL DEFAULT '1.1', revision INTEGER NOT NULL DEFAULT 1,
            supersedes_id TEXT, fingerprint TEXT, source_agent TEXT, verified_at INTEGER,
            learning_key TEXT
        )"""
    )
    columns = {row[1] for row in db.execute("PRAGMA table_info(observations)")}
    additions = {
        "schema_version": "INTEGER NOT NULL DEFAULT 1",
        "learning_version": "TEXT NOT NULL DEFAULT '1.1'",
        "revision": "INTEGER NOT NULL DEFAULT 1",
        "supersedes_id": "TEXT",
        "fingerprint": "TEXT",
        "source_agent": "TEXT",
        "verified_at": "INTEGER",
        "learning_key": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            db.execute(f"ALTER TABLE observations ADD COLUMN {name} {definition}")
    db.execute("CREATE INDEX IF NOT EXISTS idx_observations_task ON observations(task)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_observations_task_promotion ON observations(task,promotion)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_observations_learning_key ON observations(learning_key)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_observations_fingerprint ON observations(fingerprint)")
    return db


def record(root: Path, *, task: str, category: str, outcome: str, detail: str,
           command: str | None = None, approach: str | None = None,
           construct_refs: list[str] | None = None, run_id: str | None = None,
           evidence_ids: list[str] | None = None, source_agent: str | None = None,
           promotion: str = "candidate", supersedes_id: str | None = None,
           revision: int | None = None) -> dict[str, Any]:
    """Record one bounded observation or immutable learning revision.

    Exact duplicate observations are idempotent.  Revisions share a logical
    learning key and point backwards with ``supersedes_id``; historical detail
    is never overwritten or discarded.
    """
    if category not in CATEGORIES:
        raise ValueError(f"unsupported memory category: {category}")
    if outcome not in OUTCOMES:
        raise ValueError(f"unsupported outcome: {outcome}")
    if promotion not in PROMOTIONS:
        raise ValueError(f"unsupported promotion state: {promotion}")
    task_clean = _clean(task, 1000)
    detail_clean = _clean(detail)
    command_clean = _clean(command, 1000) if command else None
    approach_clean = _clean(approach, 1600) if approach else None
    refs = sorted({str(x).strip() for x in (construct_refs or []) if str(x).strip()})
    evidence = sorted({str(x).strip() for x in (evidence_ids or []) if str(x).strip()})
    logical_key = _logical_key(task_clean, category, command_clean, approach_clean)
    fingerprint = _observation_fingerprint(task_clean, category, outcome, command_clean, approach_clean, detail_clean, refs, evidence)
    now = int(time.time())
    root = Path(root)
    dbp = _db_path(root)
    audit = dbp.with_name("task-memory.jsonl")

    with _process_lock(dbp.with_name("task-memory.lock")):
        with _connect(root) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute("SELECT id FROM observations WHERE fingerprint=?", (fingerprint,)).fetchone()
                if existing:
                    row = db.execute("SELECT * FROM observations WHERE id=?", (existing[0],)).fetchone()
                    db.execute("COMMIT")
                    return _row_to_dict(row)
                if revision is None:
                    latest = db.execute("SELECT COALESCE(MAX(revision),0) FROM observations WHERE learning_key=?", (logical_key,)).fetchone()[0]
                    revision = int(latest or 0) + 1
                memory_id = _id(logical_key, revision, fingerprint)
                row = {
                    "id": memory_id, "schema_version": SCHEMA_VERSION, "learning_version": LEARNING_VERSION,
                    "recorded_at": now, "task": task_clean, "category": category, "outcome": outcome,
                    "detail": detail_clean, "command": command_clean, "approach": approach_clean,
                    "construct_refs": refs, "run_id": str(run_id) if run_id else None, "evidence_ids": evidence,
                    "status": "revision" if supersedes_id else "observation", "promotion": promotion,
                    "revision": int(revision), "supersedes_id": supersedes_id, "fingerprint": fingerprint,
                    "source_agent": _clean(source_agent, 300) if source_agent else None,
                    "verified_at": now if promotion == "verified" else None, "learning_key": logical_key,
                }
                db.execute(
                    """INSERT INTO observations
                    (id,recorded_at,task,category,outcome,detail,command,approach,construct_refs,run_id,
                     evidence_ids,status,promotion,schema_version,learning_version,revision,supersedes_id,
                     fingerprint,source_agent,verified_at,learning_key)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (memory_id, now, task_clean, category, outcome, detail_clean, command_clean, approach_clean,
                     json.dumps(refs), row["run_id"], json.dumps(evidence), row["status"], promotion, SCHEMA_VERSION,
                     LEARNING_VERSION, revision, supersedes_id, fingerprint, row["source_agent"], row["verified_at"], logical_key),
                )
                if supersedes_id:
                    db.execute("UPDATE observations SET promotion='superseded' WHERE id=? AND promotion!='rejected'", (supersedes_id,))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        with audit.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def revise(root: Path, supersedes_id: str, *, outcome: str | None = None, detail: str,
           promotion: str = "verified", evidence_ids: list[str] | None = None,
           source_agent: str = "dream-cycle") -> dict[str, Any]:
    """Create a new immutable revision while retaining the complete old record."""
    with _process_lock(_db_path(Path(root)).with_name("task-memory.lock")):
        with _connect(Path(root)) as db:
            old = db.execute("SELECT * FROM observations WHERE id=?", (supersedes_id,)).fetchone()
    if not old:
        raise KeyError(f"learning observation not found: {supersedes_id}")
    previous = _row_to_dict(old)
    return record(
        root, task=previous["task"], category=previous["category"], outcome=outcome or previous["outcome"],
        detail=detail, command=previous["command"], approach=previous["approach"],
        construct_refs=previous["construct_refs"], run_id=previous["run_id"],
        evidence_ids=evidence_ids if evidence_ids is not None else previous["evidence_ids"],
        source_agent=source_agent, promotion=promotion, supersedes_id=supersedes_id,
        revision=int(previous["revision"]) + 1,
    )


def _row_to_dict(raw: tuple | None) -> dict[str, Any]:
    if not raw:
        return {}
    return {
        "id": raw[0], "recorded_at": raw[1], "task": raw[2], "category": raw[3], "outcome": raw[4],
        "detail": raw[5], "command": raw[6], "approach": raw[7], "construct_refs": json.loads(raw[8] or "[]"),
        "run_id": raw[9], "evidence_ids": json.loads(raw[10] or "[]"), "status": raw[11], "promotion": raw[12],
        "schema_version": raw[13], "learning_version": raw[14], "revision": raw[15], "supersedes_id": raw[16],
        "fingerprint": raw[17], "source_agent": raw[18], "verified_at": raw[19], "learning_key": raw[20],
    }


def approach_history(root: Path, approach: str, limit: int = 20, *, exact: bool = True) -> list[dict[str, Any]]:
    """Return preserved observations for an approach without relevance truncation.

    Exact mode is used for capability attribution so similarly named options
    cannot contaminate one another. Prefix mode is available for broader
    approach-family history.
    """
    dbp = _db_path(Path(root))
    value = _clean(approach, 1600)
    with _process_lock(dbp.with_name("task-memory.lock")):
        with _connect(Path(root)) as db:
            if exact:
                rows = db.execute(
                    "SELECT * FROM observations WHERE promotion!='rejected' AND approach=? "
                    "ORDER BY recorded_at DESC, revision DESC LIMIT ?",
                    (value, max(1, min(100, int(limit)))),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM observations WHERE promotion!='rejected' AND approach LIKE ? "
                    "ORDER BY recorded_at DESC, revision DESC LIMIT ?",
                    (value + "%", max(1, min(100, int(limit)))),
                ).fetchall()
    return [_row_to_dict(row) for row in rows]


def relevant(root: Path, task: str, limit: int = 20) -> list[dict[str, Any]]:
    dbp = _db_path(Path(root))
    terms = {x.lower() for x in str(task).split() if len(x) > 2}
    found = []
    with _process_lock(dbp.with_name("task-memory.lock")):
        with _connect(Path(root)) as db:
            rows = db.execute("SELECT * FROM observations WHERE promotion!='rejected' ORDER BY recorded_at DESC").fetchall()
    for raw in rows:
        row = _row_to_dict(raw)
        haystack = " ".join(str(row.get(k, "")) for k in ("task", "detail", "command", "approach", "construct_refs")).lower()
        score = sum(1 for term in terms if term in haystack)
        if score or row["outcome"] in {"failed", "regressed"}:
            # Failed/regressed patterns are intentionally ranked highly: they
            # are the collective "do not repeat this" knowledge.
            failure_boost = 3 if row["outcome"] in {"failed", "regressed"} else 0
            verified_boost = 2 if row["promotion"] == "verified" else 0
            found.append((score + failure_boost, verified_boost, row["recorded_at"], row))
    found.sort(key=lambda item: (-item[0], -item[1], -item[2]))
    return [item[3] for item in found[: max(0, min(100, int(limit)))]]


def guidance(root: Path, task: str, limit: int = 3000) -> str:
    rows = relevant(root, task)
    if not rows:
        return "No task-specific historical learning."
    lines = [
        "## Historical durable learning",
        "Use this as bounded evidence. Verify it against the current repository and environment.",
        "Prioritize VERIFIED failures/regressions as explicit anti-patterns and avoid repeating them unless evidence shows the condition has changed.",
    ]
    for row in rows:
        lines.append(f"- {str(row.get('promotion','candidate')).upper()} {str(row.get('outcome','unknown')).upper()} [{row.get('category','unknown')}] rev={row.get('revision',1)} {row.get('detail','')}")
        if row.get("command"):
            lines.append(f"  command: {row['command']}")
        if row.get("approach"):
            lines.append(f"  approach: {row['approach']}")
        if row.get("evidence_ids"):
            lines.append(f"  evidence: {', '.join(row['evidence_ids'])}")
    text = "\n".join(lines)
    return text if len(text) <= limit else text[: max(200, limit - 40)] + "\n... [learning context compacted]"


def history(root: Path, learning_key: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return all preserved revisions for audit/debugging, newest first."""
    with _process_lock(_db_path(Path(root)).with_name("task-memory.lock")):
        with _connect(Path(root)) as db:
            rows = db.execute("SELECT * FROM observations WHERE learning_key=? ORDER BY revision DESC LIMIT ?", (learning_key, max(1, int(limit)))).fetchall()
    return [_row_to_dict(row) for row in rows]


__all__ = ["SCHEMA_VERSION", "LEARNING_VERSION", "record", "revise", "relevant", "guidance", "history", "approach_history"]
