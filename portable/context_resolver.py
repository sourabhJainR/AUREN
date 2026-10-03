"""Resolve durable graph and memory context into AUREN's existing context packer.

The resolver is intentionally an adapter: it does not execute agents, mutate
repositories, or create another memory store. It gathers bounded context and
hands it to ``ContextEngine`` for the existing deterministic ranking and
compaction rules.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Sequence

from .agent_capabilities import MemoryRecord, PersistentMemory
from .context_engine import ContextEngine, ContextItem
from .context_graph import ContextGraph, ContextNode


@dataclass(frozen=True)
class ContextResolution:
    task: str
    node_id: str | None
    workspace_id: str | None
    pack: str
    selected: tuple[str, ...]
    omitted: tuple[str, ...]
    digest: str


class ContextResolver:
    """Bridge durable context sources into the canonical ContextEngine."""

    def __init__(self, memory: PersistentMemory, graph: ContextGraph, engine: ContextEngine | None = None) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        if not isinstance(graph, ContextGraph):
            raise TypeError("graph must be a ContextGraph instance")
        if graph.memory.path != memory.path:
            raise ValueError("memory and graph must use the same canonical database")
        self.memory = memory
        self.graph = graph
        self.engine = engine or ContextEngine()

    def resolve(self, task: str, *, node_id: str | None = None, workspace_id: str | None = None,
                required: Sequence[str] = (), memory_limit: int = 12, graph_limit: int = 24) -> ContextResolution:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task is required")
        if memory_limit < 0 or graph_limit < 0:
            raise ValueError("context limits cannot be negative")

        items: list[ContextItem] = [ContextItem("contract", task.strip(), "task", 100, True)]
        items.extend(ContextItem("contract", value.strip(), "required", 110, True) for value in required if isinstance(value, str) and value.strip())
        source_ids: list[str] = []
        graph_ids: list[str] = []

        if node_id:
            node = self.graph.get_node(node_id)
            if node is None:
                raise KeyError(f"unknown graph node: {node_id}")
            if self._private(node):
                raise PermissionError("private task context cannot be resolved")
            items.append(self._node_item(node, 100))
            source_ids.append(node.node_id)
            if graph_limit:
                for edge in self.graph.neighbors(node.node_id, direction="out", limit=graph_limit):
                    target = self.graph.get_node(edge.target_id)
                    if target is None or self._private(target):
                        continue
                    items.append(ContextItem("graph", f"{node.label} --{edge.relation}--> {target.label}", edge.source, int(edge.confidence * 100), edge.confidence >= 0.9, (edge.edge_id,)))
                    items.append(self._node_item(target, int(edge.confidence * 100)))
                    graph_ids.append(edge.edge_id)

        if workspace_id:
            workspace = self.graph.get_node(workspace_id)
            if workspace is None:
                raise KeyError(f"unknown workspace node: {workspace_id}")
            if self._private(workspace):
                raise PermissionError("private workspace cannot be used as shared context")
            items.append(self._node_item(workspace, 95))
            source_ids.append(workspace.node_id)
            if node_id and graph_limit:
                linked = self.graph.neighbors(node_id, relation="in_workspace", direction="out", limit=graph_limit)
                if not any(edge.target_id == workspace_id for edge in linked):
                    raise PermissionError("task is not linked to the requested workspace")

        memory_rows: list[MemoryRecord] = []
        included_memory_ids: list[str] = []
        if memory_limit:
            memory_rows = self.memory.search(self.graph.project, task, limit=memory_limit)
            for record in memory_rows:
                if record.category.startswith("private:"):
                    continue
                items.extend(self.engine.from_memory([{
                    "text": record.text,
                    "kind": record.category,
                    "agent": record.project,
                    "confidence": record.confidence,
                    "verified": record.verified,
                    "evidence": [record.id],
                }]))
                included_memory_ids.append(record.id)

        selected_items = self.engine.select(items, required=required)
        pack = self.engine.pack(selected_items)
        selected = tuple(self._selection_key(item) for item in selected_items)
        selected_texts = {item.text for item in selected_items}
        omitted = tuple(dict.fromkeys(value for value in required if value not in selected_texts))
        digest_payload = {
            "task": task.strip(),
            "node_id": node_id,
            "workspace_id": workspace_id,
            "sources": sorted(source_ids),
            "graph": sorted(graph_ids),
            "memory": sorted(included_memory_ids),
            "selected": selected,
            "omitted": omitted,
            "pack": pack,
        }
        digest = hashlib.sha256(json.dumps(digest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()[:16]
        return ContextResolution(task.strip(), node_id, workspace_id, pack, selected, omitted, digest)

    @staticmethod
    def _private(node: ContextNode) -> bool:
        return bool(node.properties.get("private", False)) or node.source.lower() == "private"

    @staticmethod
    def _node_item(node: ContextNode, priority: int) -> ContextItem:
        return ContextItem(node.kind, node.label, node.source, priority, node.confidence >= 0.9, (node.node_id,))

    @staticmethod
    def _selection_key(item: ContextItem) -> str:
        refs = ",".join(item.refs)
        return f"{item.kind}:{refs or item.source}:{item.text}"


__all__ = ["ContextResolution", "ContextResolver"]
