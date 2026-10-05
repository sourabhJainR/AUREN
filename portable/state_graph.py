"""Explicit StateGraph and GraphAgentTeam execution contracts for AUREN."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from .orchestration import Graph, Node, NodeKind, NodeStatus, OrchestrationRun, Orchestrator

@dataclass(frozen=True)
class StateTransition:
    source: str
    target: str
    condition: Callable[[Mapping[str, Any]], bool] | None = None

class StateGraph:
    """Persistent-supervisor-friendly state machine over the existing Graph primitive."""
    def __init__(self, graph: Graph, transitions: tuple[StateTransition, ...] = ()) -> None:
        self.graph = graph
        self.transitions = transitions
        self.states = tuple(node.name for node in graph.order())
        self._validate_transitions()

    @classmethod
    def from_nodes(cls, nodes: list[Node], transitions: tuple[StateTransition, ...] = ()) -> "StateGraph":
        return cls(Graph(nodes), transitions)

    def _validate_transitions(self) -> None:
        names = set(self.states)
        for t in self.transitions:
            if t.source not in names or t.target not in names:
                raise ValueError(f"invalid StateGraph transition: {t.source}->{t.target}")

    def digest(self) -> str:
        import hashlib, json
        payload = {"graph": self.graph.digest(), "transitions": [(t.source,t.target) for t in self.transitions]}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def run(self, task_id: str, intent: str, context: Mapping[str, Any] | None = None, *,
            max_total_attempts: int = 32) -> OrchestrationRun:
        return Orchestrator(self.graph, max_total_attempts=max_total_attempts).run(task_id, intent, context)

class GraphAgentTeam:
    """Explicit specialist team facade; execution remains owned by the supervisor."""
    def __init__(self, graph: StateGraph, agents: Mapping[str, Any] | None = None) -> None:
        self.graph = graph
        self.agents = dict(agents or {})

    def add_agent(self, name: str, agent: Any) -> None:
        if not name.strip():
            raise ValueError("agent name is required")
        self.agents[name] = agent

    def required_personas(self, goal: str) -> tuple[str, ...]:
        text = goal.lower()
        personas = ["pm", "backend", "reviewer", "enduser"]
        if any(x in text for x in ("ui", "frontend", "web", "screen")):
            personas.insert(1, "frontend")
        if any(x in text for x in ("db", "database", "schema", "sql", "migration")):
            personas.insert(1, "dba")
        if any(x in text for x in ("security", "privacy", "auth", "secret", "permission")):
            personas.insert(1, "security_privacy")
        return tuple(dict.fromkeys(personas))

    def run(self, task_id: str, intent: str, context: Mapping[str, Any] | None = None) -> OrchestrationRun:
        return self.graph.run(task_id, intent, context)

__all__ = ["StateGraph", "StateTransition", "GraphAgentTeam"]
