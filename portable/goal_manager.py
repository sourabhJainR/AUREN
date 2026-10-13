"""Durable mission and goal state for long-horizon AUREN work."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

from .persistent_memory import PersistentMemory

_STATUSES = frozenset({"open", "active", "blocked", "completed", "abandoned"})


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _items(values: tuple[str, ...]) -> tuple[str, ...]:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("goal metadata values must be non-empty strings")
    return tuple(sorted(set(value.strip() for value in values)))


@dataclass(frozen=True)
class Goal:
    goal_id: str
    title: str
    description: str = ""
    parent_id: str | None = None
    priority: int = 50
    status: str = "open"
    created_at: str = ""
    deadline_at: str | None = None
    progress: float = 0.0
    dependencies: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (("goal_id", self.goal_id), ("title", self.title)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.parent_id == self.goal_id:
            raise ValueError("goal cannot be its own parent")
        if not 0 <= self.priority <= 100:
            raise ValueError("priority must be between 0 and 100")
        if self.status not in _STATUSES:
            raise ValueError("invalid goal status")
        if not 0 <= self.progress <= 1:
            raise ValueError("progress must be between 0 and 1")
        if not self.created_at:
            object.__setattr__(self, "created_at", _utc())
        object.__setattr__(self, "created_at", _timestamp(self.created_at))
        if self.deadline_at is not None:
            object.__setattr__(self, "deadline_at", _timestamp(self.deadline_at))
        object.__setattr__(self, "dependencies", _items(self.dependencies))
        if self.goal_id in self.dependencies:
            raise ValueError("goal cannot depend on itself")
        object.__setattr__(self, "constraints", _items(self.constraints))


class GoalManager:
    """Persist goals with hierarchy, deadlines, dependencies and progress."""

    def __init__(self, memory: PersistentMemory, project: str, *, max_goals: int = 20_000) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project is required")
        if max_goals < 1:
            raise ValueError("max_goals must be positive")
        self.memory = memory
        self.project = project.strip()
        self.max_goals = max_goals
        with sqlite3.connect(memory.path, timeout=10) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS goals(
                project TEXT NOT NULL, goal_id TEXT NOT NULL, title TEXT NOT NULL,
                description TEXT NOT NULL, parent_id TEXT, priority INTEGER NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, deadline_at TEXT,
                progress REAL NOT NULL DEFAULT 0, dependencies TEXT NOT NULL DEFAULT '[]',
                constraints TEXT NOT NULL DEFAULT '[]', PRIMARY KEY(project, goal_id))""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(goals)").fetchall()}
            additions = {
                "deadline_at": "TEXT",
                "progress": "REAL NOT NULL DEFAULT 0",
                "dependencies": "TEXT NOT NULL DEFAULT '[]'",
                "constraints": "TEXT NOT NULL DEFAULT '[]'",
            }
            for name, ddl in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE goals ADD COLUMN {name} {ddl}")
            db.execute("CREATE INDEX IF NOT EXISTS idx_goals_parent ON goals(project, parent_id, priority, goal_id)")

    def _row(self, row: tuple[Any, ...] | None) -> Goal | None:
        return Goal(row[0], row[1], row[2], row[3], int(row[4]), row[5], row[6], row[7], float(row[8]),
                    tuple(json.loads(row[9] or "[]")), tuple(json.loads(row[10] or "[]"))) if row else None

    def _select(self) -> str:
        return "goal_id,title,description,parent_id,priority,status,created_at,deadline_at,progress,dependencies,constraints"

    def create(self, goal: Goal) -> Goal:
        with sqlite3.connect(self.memory.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            if goal.parent_id and not db.execute("SELECT 1 FROM goals WHERE project=? AND goal_id=?", (self.project, goal.parent_id)).fetchone():
                raise KeyError("parent goal does not exist")
            for dependency in goal.dependencies:
                if not db.execute("SELECT 1 FROM goals WHERE project=? AND goal_id=?", (self.project, dependency)).fetchone():
                    raise KeyError(f"dependency goal does not exist: {dependency}")
            existing = db.execute(f"SELECT {self._select()} FROM goals WHERE project=? AND goal_id=?", (self.project, goal.goal_id)).fetchone()
            expected = (goal.goal_id, goal.title, goal.description, goal.parent_id, goal.priority, goal.status, goal.created_at,
                        goal.deadline_at, goal.progress, json.dumps(goal.dependencies), json.dumps(goal.constraints))
            if existing:
                if existing != expected:
                    raise ValueError("goal_id already exists with different content")
                return goal
            count = db.execute("SELECT COUNT(*) FROM goals WHERE project=?", (self.project,)).fetchone()[0]
            if count >= self.max_goals:
                raise ValueError("goal budget exceeded")
            db.execute("INSERT INTO goals VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (self.project, *expected))
        return goal

    def get(self, goal_id: str) -> Goal | None:
        with sqlite3.connect(self.memory.path, timeout=10) as db:
            row = db.execute(f"SELECT {self._select()} FROM goals WHERE project=? AND goal_id=?", (self.project, goal_id)).fetchone()
        return self._row(row)

    def children(self, parent_id: str | None = None, *, limit: int = 100) -> tuple[Goal, ...]:
        if limit < 1:
            return ()
        with sqlite3.connect(self.memory.path, timeout=10) as db:
            where = "parent_id IS NULL" if parent_id is None else "parent_id=?"
            params: tuple[Any, ...] = (self.project,) if parent_id is None else (self.project, parent_id)
            rows = db.execute(f"SELECT {self._select()} FROM goals WHERE project=? AND {where} ORDER BY priority DESC,goal_id LIMIT ?", params + (limit,)).fetchall()
        return tuple(goal for goal in (self._row(row) for row in rows) if goal is not None)

    def set_status(self, goal_id: str, status: str) -> Goal:
        if status not in _STATUSES:
            raise ValueError("invalid goal status")
        goal = self.get(goal_id)
        if goal is None:
            raise KeyError("goal does not exist")
        if goal.status in {"completed", "abandoned"} and status != goal.status:
            raise ValueError("terminal goal cannot be reopened")
        updated = Goal(goal.goal_id, goal.title, goal.description, goal.parent_id, goal.priority, status,
                       goal.created_at, goal.deadline_at, goal.progress, goal.dependencies, goal.constraints)
        with sqlite3.connect(self.memory.path, timeout=10) as db:
            db.execute("UPDATE goals SET status=? WHERE project=? AND goal_id=?", (status, self.project, goal_id))
        return updated

    def set_progress(self, goal_id: str, progress: float) -> Goal:
        goal = self.get(goal_id)
        if goal is None:
            raise KeyError("goal does not exist")
        updated = Goal(goal.goal_id, goal.title, goal.description, goal.parent_id, goal.priority, goal.status,
                       goal.created_at, goal.deadline_at, progress, goal.dependencies, goal.constraints)
        with sqlite3.connect(self.memory.path, timeout=10) as db:
            db.execute("UPDATE goals SET progress=? WHERE project=? AND goal_id=?", (progress, self.project, goal_id))
        return updated

    def ready(self, *, limit: int = 100, now: str | None = None) -> tuple[Goal, ...]:
        if limit < 1:
            return ()
        current = _timestamp(now) if now else _utc()
        with sqlite3.connect(self.memory.path, timeout=10) as db:
            rows = db.execute(f"SELECT {self._select()} FROM goals WHERE project=? AND status IN ('open','active') ORDER BY priority DESC,goal_id", (self.project,)).fetchall()
            status_rows = db.execute("SELECT goal_id,status FROM goals WHERE project=?", (self.project,)).fetchall()
        statuses = {goal_id: status for goal_id, status in status_rows}
        ready: list[Goal] = []
        for row in rows:
            goal = self._row(row)
            if goal is None:
                continue
            if goal.deadline_at and goal.deadline_at < current and goal.progress < 1:
                continue
            if any(statuses.get(dep) != "completed" for dep in goal.dependencies):
                continue
            ready.append(goal)
            if len(ready) >= limit:
                break
        return tuple(ready)


__all__ = ["Goal", "GoalManager"]
