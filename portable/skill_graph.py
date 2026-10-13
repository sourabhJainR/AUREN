"""Persistent, cycle-safe skill dependency graph for AUREN."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .persistent_memory import PersistentMemory


def _clean(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value.strip()


def _items(values: Iterable[str]) -> frozenset[str]:
    return frozenset(sorted(_clean(value, "item") for value in values))


@dataclass(frozen=True)
class SkillNode:
    name: str
    kind: str = "skill"
    prerequisites: frozenset[str] = frozenset()
    evidence_ids: frozenset[str] = frozenset()
    failure_modes: frozenset[str] = frozenset()
    contexts: frozenset[str] = frozenset()
    validated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _clean(self.name, "name"))
        object.__setattr__(self, "kind", _clean(self.kind, "kind"))
        object.__setattr__(self, "prerequisites", _items(self.prerequisites))
        object.__setattr__(self, "evidence_ids", _items(self.evidence_ids))
        object.__setattr__(self, "failure_modes", _items(self.failure_modes))
        object.__setattr__(self, "contexts", _items(self.contexts))


class SkillGraph:
    """Project-scoped skill graph with evidence-backed readiness and cycle checks."""

    def __init__(self, memory: PersistentMemory, project: str) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        self.memory = memory
        self.project = _clean(project, "project")
        with self.memory._lock, self.memory._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS skill_nodes(
                project TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
                evidence_ids TEXT NOT NULL, failure_modes TEXT NOT NULL, contexts TEXT NOT NULL,
                validated INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(project,name))""")
            db.execute("""CREATE TABLE IF NOT EXISTS skill_dependencies(
                project TEXT NOT NULL, skill TEXT NOT NULL, prerequisite TEXT NOT NULL,
                PRIMARY KEY(project,skill,prerequisite))""")

    def upsert(self, node: SkillNode) -> None:
        if not isinstance(node, SkillNode):
            raise ValueError("node must be a SkillNode")
        now = datetime.now(timezone.utc).isoformat()
        with self.memory._lock, self.memory._connect() as db:
            missing = [name for name in node.prerequisites if db.execute(
                "SELECT 1 FROM skill_nodes WHERE project=? AND name=?", (self.project, name)
            ).fetchone() is None]
            if missing:
                raise KeyError(f"unknown prerequisite skill: {missing[0]}")
            db.execute(
                "INSERT INTO skill_nodes VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(project,name) DO UPDATE SET kind=excluded.kind,evidence_ids=excluded.evidence_ids,"
                "failure_modes=excluded.failure_modes,contexts=excluded.contexts,validated=excluded.validated,updated_at=excluded.updated_at",
                (self.project, node.name, node.kind, json.dumps(sorted(node.evidence_ids)),
                 json.dumps(sorted(node.failure_modes)), json.dumps(sorted(node.contexts)), int(node.validated), now, now),
            )
            db.execute("DELETE FROM skill_dependencies WHERE project=? AND skill=?", (self.project, node.name))
            for prerequisite in node.prerequisites:
                if prerequisite == node.name:
                    raise ValueError("skill cannot depend on itself")
                db.execute("INSERT INTO skill_dependencies VALUES(?,?,?)", (self.project, node.name, prerequisite))
            if self._has_cycle(db):
                raise ValueError("skill dependency cycle detected")

    def add_dependency(self, skill: str, prerequisite: str) -> None:
        skill = _clean(skill, "skill")
        prerequisite = _clean(prerequisite, "prerequisite")
        if skill == prerequisite:
            raise ValueError("skill cannot depend on itself")
        with self.memory._lock, self.memory._connect() as db:
            for name in (skill, prerequisite):
                if db.execute("SELECT 1 FROM skill_nodes WHERE project=? AND name=?", (self.project, name)).fetchone() is None:
                    raise KeyError(f"unknown skill: {name}")
            db.execute("INSERT OR IGNORE INTO skill_dependencies VALUES(?,?,?)", (self.project, skill, prerequisite))
            if self._has_cycle(db):
                raise ValueError("skill dependency cycle detected")

    def _has_cycle(self, db: sqlite3.Connection) -> bool:
        rows = db.execute("SELECT skill,prerequisite FROM skill_dependencies WHERE project=?", (self.project,)).fetchall()
        edges: dict[str, set[str]] = {}
        for skill, prerequisite in rows:
            edges.setdefault(skill, set()).add(prerequisite)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            if any(visit(child) for child in edges.get(node, ())):
                return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(visit(node) for node in edges)

    def missing_prerequisites(self, skill: str) -> tuple[str, ...]:
        skill = _clean(skill, "skill")
        with self.memory._lock, self.memory._connect() as db:
            if db.execute("SELECT 1 FROM skill_nodes WHERE project=? AND name=?", (self.project, skill)).fetchone() is None:
                return ()
            rows = db.execute(
                "SELECT d.prerequisite FROM skill_dependencies d "
                "LEFT JOIN skill_nodes n ON n.project=d.project AND n.name=d.prerequisite "
                "WHERE d.project=? AND d.skill=? AND (n.name IS NULL OR n.validated=0 OR n.evidence_ids='[]') "
                "ORDER BY d.prerequisite",
                (self.project, skill),
            ).fetchall()
        return tuple(row[0] for row in rows)

    def ready(self, skill: str) -> bool:
        skill = _clean(skill, "skill")
        with self.memory._lock, self.memory._connect() as db:
            row = db.execute("SELECT validated,evidence_ids FROM skill_nodes WHERE project=? AND name=?", (self.project, skill)).fetchone()
        if row is None or not bool(row[0]) or not json.loads(row[1]):
            return False
        for ancestor in self.ancestors(skill):
            with self.memory._lock, self.memory._connect() as db:
                ancestor_row = db.execute("SELECT validated,evidence_ids FROM skill_nodes WHERE project=? AND name=?", (self.project, ancestor)).fetchone()
            if ancestor_row is None or not bool(ancestor_row[0]) or not json.loads(ancestor_row[1]):
                return False
        return True

    def ancestors(self, skill: str, *, limit: int = 100) -> tuple[str, ...]:
        skill = _clean(skill, "skill")
        if limit < 1:
            return ()
        with self.memory._lock, self.memory._connect() as db:
            edges = db.execute("SELECT skill,prerequisite FROM skill_dependencies WHERE project=?", (self.project,)).fetchall()
        graph: dict[str, set[str]] = {}
        for child, parent in edges:
            graph.setdefault(child, set()).add(parent)
        found: set[str] = set()
        stack = list(graph.get(skill, ()))
        while stack and len(found) < limit:
            node = stack.pop()
            if node in found:
                continue
            found.add(node)
            stack.extend(graph.get(node, ()))
        return tuple(sorted(found))[:limit]


__all__ = ["SkillGraph", "SkillNode"]
