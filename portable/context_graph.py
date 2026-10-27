"""Bounded, provenance-aware context graph backed by AUREN PersistentMemory storage.

This module deliberately does not create a second memory store. It adds two
small relationship tables to the same SQLite database used by
``PersistentMemory`` and exposes deterministic graph operations for the
orchestrator. Execution, policy, verification and promotion remain elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any, Mapping, Sequence

from .agent_capabilities import PersistentMemory


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _properties(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("graph properties must decode to an object")
    return decoded


@dataclass(frozen=True)
class ContextNode:
    node_id: str
    kind: str
    label: str
    source: str
    confidence: float = 1.0
    properties: Mapping[str, Any] | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        for name, value in (("node_id", self.node_id), ("kind", self.kind), ("label", self.label), ("source", self.source)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.properties is None:
            object.__setattr__(self, "properties", {})
        if not isinstance(self.properties, Mapping):
            raise TypeError("properties must be a mapping")
        try:
            json.dumps(dict(self.properties), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        except (TypeError, ValueError) as exc:
            raise TypeError("properties must be JSON-compatible") from exc
        if not self.created_at:
            object.__setattr__(self, "created_at", _utc())


@dataclass(frozen=True)
class ContextEdge:
    edge_id: str
    source_id: str
    relation: str
    target_id: str
    source: str
    confidence: float = 1.0
    properties: Mapping[str, Any] | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        for name, value in (("edge_id", self.edge_id), ("source_id", self.source_id), ("relation", self.relation), ("target_id", self.target_id), ("source", self.source)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.properties is None:
            object.__setattr__(self, "properties", {})
        if not isinstance(self.properties, Mapping):
            raise TypeError("properties must be a mapping")
        try:
            json.dumps(dict(self.properties), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        except (TypeError, ValueError) as exc:
            raise TypeError("properties must be JSON-compatible") from exc
        if not self.created_at:
            object.__setattr__(self, "created_at", _utc())


class ContextGraph:
    """Deterministic relationship graph over the canonical AUREN memory DB."""

    def __init__(self, memory: PersistentMemory, project: str, *, max_nodes: int = 50_000, max_edges: int = 200_000) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project is required")
        if max_nodes < 1 or max_edges < 1:
            raise ValueError("graph bounds must be positive")
        self.memory = memory
        self.project = project
        self.max_nodes = max_nodes
        self.max_edges = max_edges
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.memory.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """CREATE TABLE IF NOT EXISTS context_nodes(
                    project TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    properties TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project, node_id));
                CREATE TABLE IF NOT EXISTS context_edges(
                    project TEXT NOT NULL,
                    edge_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    properties TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project, edge_id),
                    UNIQUE(project, source_id, relation, target_id));
                CREATE INDEX IF NOT EXISTS idx_context_edges_source
                    ON context_edges(project, source_id, relation, target_id);
                CREATE INDEX IF NOT EXISTS idx_context_edges_target
                    ON context_edges(project, target_id, relation, source_id);"""
            )

    def upsert_node(self, node: ContextNode) -> ContextNode:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT kind,label,source,confidence,properties,created_at FROM context_nodes WHERE project=? AND node_id=?",
                (self.project, node.node_id),
            ).fetchone()
            if row:
                existing = self._node(node.node_id, row)
                if existing == node:
                    return existing
                existing_properties = dict(existing.properties or {})
                incoming_properties = dict(node.properties or {})
                if existing_properties.get("retracted_at"):
                    if not incoming_properties.get("retracted_at"):
                        raise PermissionError("retracted context nodes cannot be revived")
                    if (
                        incoming_properties.get("retracted_at") != existing_properties.get("retracted_at")
                        or incoming_properties.get("retraction_reason") != existing_properties.get("retraction_reason")
                    ):
                        raise PermissionError("context-node retraction audit is immutable")
                updated = ContextNode(node.node_id, node.kind, node.label, node.source, node.confidence, node.properties, existing.created_at)
                db.execute(
                    "UPDATE context_nodes SET kind=?,label=?,source=?,confidence=?,properties=? WHERE project=? AND node_id=?",
                    (updated.kind, updated.label, updated.source, updated.confidence, _json(updated.properties), self.project, updated.node_id),
                )
                return updated
            count = db.execute("SELECT COUNT(*) FROM context_nodes WHERE project=?", (self.project,)).fetchone()[0]
            if count >= self.max_nodes:
                raise ValueError("context graph node budget exceeded")
            db.execute(
                "INSERT INTO context_nodes VALUES(?,?,?,?,?,?,?,?)",
                (self.project, node.node_id, node.kind, node.label, node.source, node.confidence, _json(node.properties), node.created_at),
            )
        return node

    @staticmethod
    def _node(node_id: str, row: Sequence[Any]) -> ContextNode:
        return ContextNode(node_id, row[0], row[1], row[2], float(row[3]), _properties(row[4]), row[5])

    def _get_node_unscoped(self, node_id: str) -> ContextNode | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT kind,label,source,confidence,properties,created_at FROM context_nodes WHERE project=? AND node_id=?",
                (self.project, node_id),
            ).fetchone()
        return self._node(node_id, row) if row else None

    def get_node(
        self, node_id: str, *, branch: str | None = None, source_revision: str | None = None,
    ) -> ContextNode | None:
        node = self._get_node_unscoped(node_id)
        if node is None:
            return None
        properties = dict(node.properties or {})
        if properties.get("branch") is not None and properties.get("branch") != branch:
            return None
        if properties.get("source_revision") is not None and properties.get("source_revision") != source_revision:
            return None
        return node

    def link(self, source_id: str, relation: str, target_id: str, *, source: str, confidence: float = 1.0, properties: Mapping[str, Any] | None = None, edge_id: str | None = None) -> ContextEdge:
        if not relation.strip() or not source.strip():
            raise ValueError("relation and source are required")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        source_node = self._get_node_unscoped(source_id)
        target_node = self._get_node_unscoped(target_id)
        if source_node is None or target_node is None:
            raise KeyError("both graph endpoints must exist")
        source_properties = dict(source_node.properties or {})
        target_properties = dict(target_node.properties or {})
        if source_properties.get("retracted_at") or target_properties.get("retracted_at"):
            raise PermissionError("cannot link a retracted context node")
        for scope_key in ("branch", "source_revision"):
            source_scope = source_properties.get(scope_key)
            target_scope = target_properties.get(scope_key)
            if source_scope is not None and target_scope is not None and source_scope != target_scope:
                raise PermissionError(f"cannot link context nodes across {scope_key} scopes")
        if edge_id is None:
            seed = f"{self.project}|{source_id}|{relation}|{target_id}"
            edge_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
        edge = ContextEdge(edge_id, source_id, relation, target_id, source, confidence, properties)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT edge_id,source_id,relation,target_id,source,confidence,properties,created_at FROM context_edges WHERE project=? AND source_id=? AND relation=? AND target_id=?",
                (self.project, source_id, relation, target_id),
            ).fetchone()
            if row:
                return self._edge(row)
            count = db.execute("SELECT COUNT(*) FROM context_edges WHERE project=?", (self.project,)).fetchone()[0]
            if count >= self.max_edges:
                raise ValueError("context graph edge budget exceeded")
            db.execute("INSERT INTO context_edges VALUES(?,?,?,?,?,?,?,?,?)", (self.project, edge.edge_id, edge.source_id, edge.relation, edge.target_id, edge.source, edge.confidence, _json(edge.properties), edge.created_at))
        return edge

    @staticmethod
    def _edge(row: Sequence[Any]) -> ContextEdge:
        return ContextEdge(row[0], row[1], row[2], row[3], row[4], float(row[5]), _properties(row[6]), row[7])

    def neighbors(
        self, node_id: str, relation: str | None = None, *, direction: str = "out",
        limit: int = 50, branch: str | None = None, source_revision: str | None = None,
        include_retracted: bool = False,
    ) -> tuple[ContextEdge, ...]:
        if limit < 1:
            return ()
        if direction not in {"out", "in", "both"}:
            raise ValueError("direction must be out, in, or both")
        clauses: list[str] = []
        params: list[Any] = [self.project]
        if direction in {"out", "both"}:
            clause = "source_id=?"
            params.append(node_id)
            if relation:
                clause += " AND relation=?"
                params.append(relation)
            clauses.append(f"({clause})")
        if direction in {"in", "both"}:
            clause = "target_id=?"
            params.append(node_id)
            if relation:
                clause += " AND relation=?"
                params.append(relation)
            clauses.append(f"({clause})")
        where = " OR ".join(clauses)
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT edge_id,source_id,relation,target_id,source,confidence,properties,created_at FROM context_edges WHERE project=? AND ({where}) ORDER BY relation,source_id,target_id,edge_id LIMIT ?",
                tuple(params),
            ).fetchall()
            if not rows:
                return ()
            node_ids = sorted({row[1] for row in rows} | {row[3] for row in rows})
            placeholders = ",".join("?" for _ in node_ids)
            node_rows = db.execute(
                f"SELECT node_id,properties FROM context_nodes WHERE project=? AND node_id IN ({placeholders})",
                (self.project, *node_ids),
            ).fetchall()
        node_properties = {node_id: _properties(properties) for node_id, properties in node_rows}

        def in_scope(properties: Mapping[str, Any]) -> bool:
            stored_branch = properties.get("branch")
            stored_revision = properties.get("source_revision")
            return (
                (stored_branch is None or stored_branch == branch)
                and (stored_revision is None or stored_revision == source_revision)
            )

        edges = tuple(self._edge(row) for row in rows)
        return tuple(
            edge for edge in edges
            if edge.source_id in node_properties and edge.target_id in node_properties
            and in_scope(node_properties[edge.source_id])
            and in_scope(node_properties[edge.target_id])
            and (include_retracted or (
                not node_properties[edge.source_id].get("retracted_at")
                and not node_properties[edge.target_id].get("retracted_at")
            ))
        )

    def digest(self) -> str:
        with self._connect() as db:
            nodes = db.execute("SELECT node_id,kind,label,source,confidence,properties,created_at FROM context_nodes WHERE project=? ORDER BY node_id", (self.project,)).fetchall()
            edges = db.execute("SELECT edge_id,source_id,relation,target_id,source,confidence,properties,created_at FROM context_edges WHERE project=? ORDER BY edge_id", (self.project,)).fetchall()
        payload = {"project": self.project, "nodes": nodes, "edges": edges}
        return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()[:16]


__all__ = ["ContextEdge", "ContextGraph", "ContextNode"]
