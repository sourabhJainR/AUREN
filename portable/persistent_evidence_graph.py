"""Persistent, content-addressed evidence graph for autonomous learning."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json
from .persistent_memory import PersistentMemory

@dataclass(frozen=True, slots=True)
class EvidenceNode:
    node_id: str
    kind: str
    digest: str
    metadata: tuple[tuple[str,str], ...] = ()

@dataclass(frozen=True, slots=True)
class EvidenceEdge:
    source_id: str
    relation: str
    target_id: str
    edge_digest: str

class PersistentEvidenceGraph:
    """Append-only evidence lineage store backed by the existing durable memory."""

    def __init__(self, memory: PersistentMemory, project: str) -> None:
        if not isinstance(memory, PersistentMemory): raise TypeError("memory must be PersistentMemory")
        if not project.strip(): raise ValueError("project is required")
        self.memory, self.project = memory, project.strip()
        with memory._lock, memory._connect() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS evidence_nodes(
                project TEXT NOT NULL,node_id TEXT NOT NULL,kind TEXT NOT NULL,digest TEXT NOT NULL,
                metadata_json TEXT NOT NULL,PRIMARY KEY(project,node_id));
            CREATE TABLE IF NOT EXISTS evidence_edges(
                project TEXT NOT NULL,source_id TEXT NOT NULL,relation TEXT NOT NULL,target_id TEXT NOT NULL,
                edge_digest TEXT NOT NULL,PRIMARY KEY(project,source_id,relation,target_id));""")

    @staticmethod
    def node_id(kind: str, digest: str) -> str:
        if not kind.strip() or not digest.strip(): raise ValueError("node kind and digest are required")
        return hashlib.sha256(f"{kind}:{digest}".encode()).hexdigest()

    @staticmethod
    def edge_digest(source_id: str, relation: str, target_id: str) -> str:
        return hashlib.sha256(json.dumps([source_id,relation,target_id],separators=(",",":")).encode()).hexdigest()

    def add_node(self, kind: str, digest: str, metadata: dict[str,str] | None = None) -> EvidenceNode:
        node=EvidenceNode(self.node_id(kind,digest),kind,digest,tuple(sorted((metadata or {}).items())))
        with self.memory._lock, self.memory._connect() as db:
            db.execute("INSERT OR IGNORE INTO evidence_nodes VALUES(?,?,?,?,?)",
                       (self.project,node.node_id,node.kind,node.digest,json.dumps(dict(node.metadata),sort_keys=True)))
        return node

    def add_edge(self, source: EvidenceNode, relation: str, target: EvidenceNode) -> EvidenceEdge:
        if not relation.strip(): raise ValueError("relation is required")
        edge=EvidenceEdge(source.node_id,relation,target.node_id,self.edge_digest(source.node_id,relation,target.node_id))
        with self.memory._lock, self.memory._connect() as db:
            for node in (source,target):
                row=db.execute("SELECT digest,kind FROM evidence_nodes WHERE project=? AND node_id=?",(self.project,node.node_id)).fetchone()
                if row is None or row[0] != node.digest or row[1] != node.kind:
                    raise ValueError("edge references an unregistered evidence node")
            db.execute("INSERT OR IGNORE INTO evidence_edges VALUES(?,?,?,?,?)",
                       (self.project,edge.source_id,edge.relation,edge.target_id,edge.edge_digest))
        return edge

    def get_node(self, node_id: str) -> EvidenceNode | None:
        with self.memory._lock, self.memory._connect() as db:
            row = db.execute(
                "SELECT node_id,kind,digest,metadata_json FROM evidence_nodes WHERE project=? AND node_id=?",
                (self.project, node_id),
            ).fetchone()
        if row is None:
            return None
        return EvidenceNode(row[0], row[1], row[2], tuple(sorted(json.loads(row[3]).items())))

    def nodes(self, *, kind: str | None = None, max_nodes: int = 1000) -> tuple[EvidenceNode, ...]:
        if max_nodes < 1:
            raise ValueError("max_nodes must be positive")
        with self.memory._lock, self.memory._connect() as db:
            if kind is None:
                rows = db.execute(
                    "SELECT node_id,kind,digest,metadata_json FROM evidence_nodes "
                    "WHERE project=? ORDER BY node_id LIMIT ?",
                    (self.project, max_nodes),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT node_id,kind,digest,metadata_json FROM evidence_nodes "
                    "WHERE project=? AND kind=? ORDER BY node_id LIMIT ?",
                    (self.project, kind, max_nodes),
                ).fetchall()
        return tuple(EvidenceNode(r[0], r[1], r[2], tuple(sorted(json.loads(r[3]).items()))) for r in rows)

    def lineage(self, node_id: str, *, direction: str = "both", max_edges: int = 100) -> tuple[EvidenceEdge,...]:
        if direction not in {"in","out","both"}: raise ValueError("unsupported lineage direction")
        if max_edges < 1: raise ValueError("max_edges must be positive")
        clauses=[]; params=[self.project]
        if direction=="in": clauses.append("target_id=?"); params.append(node_id)
        elif direction=="out": clauses.append("source_id=?"); params.append(node_id)
        else: clauses.append("(source_id=? OR target_id=?)"); params.extend([node_id,node_id])
        where=" AND ".join(["project=?"]+clauses)
        with self.memory._lock, self.memory._connect() as db:
            rows=db.execute(f"SELECT source_id,relation,target_id,edge_digest FROM evidence_edges WHERE {where} ORDER BY edge_digest LIMIT ?",(*params,max_edges)).fetchall()
        return tuple(EvidenceEdge(*row) for row in rows)

__all__=["EvidenceNode","EvidenceEdge","PersistentEvidenceGraph"]
