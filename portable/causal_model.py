"""Causal reasoning primitives backed by AUREN's existing ContextGraph.

Causal links are explicit, confidence-bounded and evidence-referenced. The
model distinguishes a recorded relationship from a proven causal claim and
never treats a causal edge as permission to mutate the environment.
"""
from __future__ import annotations

from dataclasses import dataclass

from .context_graph import ContextEdge, ContextGraph


@dataclass(frozen=True)
class CausalLink:
    link_id: str
    cause_id: str
    effect_id: str
    mechanism: str
    source: str
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()
    interventions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (("link_id", self.link_id), ("cause_id", self.cause_id),
                            ("effect_id", self.effect_id), ("mechanism", self.mechanism), ("source", self.source)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.cause_id == self.effect_id:
            raise ValueError("cause and effect must be different")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        for name in ("evidence", "interventions"):
            values = getattr(self, name)
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError(f"{name} must contain non-empty strings")


class CausalModel:
    """Small semantic layer using ContextGraph as the canonical graph store."""

    def __init__(self, graph: ContextGraph, *, max_links: int = 50_000) -> None:
        if not isinstance(graph, ContextGraph):
            raise TypeError("graph must be a ContextGraph instance")
        if max_links < 1:
            raise ValueError("max_links must be positive")
        self.graph = graph
        self.max_links = max_links

    def record(self, link: CausalLink) -> ContextEdge:
        if self.graph.get_node(link.cause_id) is None or self.graph.get_node(link.effect_id) is None:
            raise KeyError("both causal endpoints must exist")
        existing = self.graph.neighbors(link.cause_id, relation="causes", direction="out", limit=self.max_links)
        for edge in existing:
            if edge.target_id == link.effect_id:
                if edge.edge_id != link.link_id:
                    raise ValueError("causal link already exists with a different link_id")
                return edge
        return self.graph.link(
            link.cause_id, "causes", link.effect_id, source=link.source,
            confidence=link.confidence, edge_id=link.link_id,
            properties={"mechanism": link.mechanism, "evidence": sorted(set(link.evidence)),
                        "interventions": sorted(set(link.interventions))},
        )

    def causes_of(self, effect_id: str, *, limit: int = 50) -> tuple[ContextEdge, ...]:
        return self.graph.neighbors(effect_id, relation="causes", direction="in", limit=limit)

    def effects_of(self, cause_id: str, *, limit: int = 50) -> tuple[ContextEdge, ...]:
        return self.graph.neighbors(cause_id, relation="causes", direction="out", limit=limit)


__all__ = ["CausalLink", "CausalModel"]
