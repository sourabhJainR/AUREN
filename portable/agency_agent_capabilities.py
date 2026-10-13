"""Compatibility layer for agency-specific runtime primitives.

Canonical ownership lives in ``portable.agent_capabilities``. This module
keeps agency-facing context, tool, event and collaboration types, but does
not own a second capability catalog or a second durable memory store.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Mapping, Protocol

from .agent_capabilities import (
    CAPABILITIES,
    Capability,
    CapabilityFabric,
    MemoryRecord,
    PersistentMemory,
    ProviderAdapter,
    ProviderAdapterRegistry,
)


@dataclass(frozen=True)
class ContextItem:
    key: str
    value: Any
    priority: int = 0
    stable: bool = True
    sensitive: bool = False


@dataclass(frozen=True)
class ContextPack:
    items: tuple[ContextItem, ...]
    token_estimate: int
    omitted: tuple[str, ...] = ()
    digest: str = ""


def build_context(items: list[ContextItem], token_budget: int, reserve_tokens: int = 0) -> ContextPack:
    if token_budget <= 0 or reserve_tokens < 0 or reserve_tokens >= token_budget:
        raise ValueError("invalid context budget")
    available = token_budget - reserve_tokens
    ordered = sorted(items, key=lambda x: (-x.priority, not x.stable, x.key))
    selected: list[ContextItem] = []
    omitted: list[str] = []
    used = 0
    for item in ordered:
        size = max(1, len(str(item.value).split()))
        if used + size <= available:
            selected.append(item)
            used += size
        else:
            omitted.append(item.key)
    payload = [(x.key, str(x.value), x.priority, x.stable) for x in selected]
    digest = sha256(repr(payload).encode()).hexdigest()
    return ContextPack(tuple(selected), used, tuple(omitted), digest)


@dataclass(frozen=True)
class MemoryFact:
    """Legacy value object mapped onto the canonical PersistentMemory record."""
    key: str
    value: str
    kind: str = "semantic"
    source: str = ""
    confidence: float = 1.0
    timestamp: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


class MemoryStore:
    """Legacy adapter over the canonical AUREN PersistentMemory."""
    _KINDS = frozenset({"episodic", "semantic", "procedural"})

    def __init__(self, persist_path: str | Path | None = None) -> None:
        self._owned_temp: Path | None = None
        if persist_path is None:
            handle = tempfile.NamedTemporaryFile(prefix="auren-memory-", suffix=".sqlite", delete=False)
            handle.close()
            self._owned_temp = Path(handle.name)
            persist_path = self._owned_temp
        self.persist_path = Path(persist_path).expanduser().resolve()
        self._store = PersistentMemory(self.persist_path, require_approval=False)

    @staticmethod
    def _category(fact: MemoryFact) -> str:
        return f"legacy:{fact.kind}:{fact.key}"

    @staticmethod
    def _to_fact(record: MemoryRecord) -> MemoryFact:
        parts = record.category.split(":", 2)
        if len(parts) == 3 and parts[0] == "legacy":
            kind, key = parts[1], parts[2]
        else:
            kind, key = "semantic", record.category
        return MemoryFact(key=key, value=record.text, kind=kind, source=record.project, confidence=record.confidence, timestamp=_timestamp(record.created_at))

    def upsert(self, fact: MemoryFact) -> MemoryFact:
        if fact.kind not in self._KINDS:
            raise ValueError("unsupported memory kind")
        if not 0 <= fact.confidence <= 1 or not fact.key.strip():
            raise ValueError("invalid memory fact")
        existing = self.get(fact.key)
        if existing and existing.confidence > fact.confidence and existing.value != fact.value:
            return existing
        record = self._store.remember(
            fact.source or "default", self._category(fact), fact.value,
            confidence=fact.confidence, verified=fact.confidence >= 0.9, approved=True,
        )
        if record is None:
            raise RuntimeError("canonical memory rejected write")
        return fact

    def get(self, key: str) -> MemoryFact | None:
        rows = self._store.search("default", key, limit=20)
        matches = [self._to_fact(row) for row in rows if row.category.startswith("legacy:") and row.category.rsplit(":", 1)[-1] == key]
        return matches[0] if matches else None

    def search(self, query: str, limit: int = 8) -> tuple[MemoryFact, ...]:
        if limit < 1:
            return ()
        rows = self._store.search("default", query, limit=limit)
        return tuple(self._to_fact(row) for row in rows if row.category.startswith("legacy:"))

    def snapshot(self) -> tuple[MemoryFact, ...]:
        return ()

    def close(self) -> None:
        self._store.close()
        if self._owned_temp:
            try:
                self._owned_temp.unlink(missing_ok=True)
            except OSError:
                pass


def _timestamp(value: str) -> float:
    try:
        from datetime import datetime
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError, OverflowError):
        return time.time()


@dataclass(frozen=True)
class ToolSpec:
    """Tool transport metadata; capability ownership remains in CapabilityFabric."""
    name: str
    description: str
    category: str
    risk: str = "low"
    permissions: tuple[str, ...] = ()
    handler: Callable[..., Any] | None = None


class ToolPolicy(Protocol):
    def allow(self, tool: ToolSpec, arguments: Mapping[str, Any]) -> bool: ...


@dataclass(frozen=True)
class AllowlistedToolPolicy:
    allowed_tools: frozenset[str] = frozenset()
    allowed_permissions: frozenset[str] = frozenset()
    max_risk: str = "medium"

    def allow(self, tool: ToolSpec, arguments: Mapping[str, Any]) -> bool:
        if self.allowed_tools and tool.name not in self.allowed_tools:
            return False
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        if order.get(tool.risk, 3) > order.get(self.max_risk, 1):
            return False
        return set(tool.permissions).issubset(self.allowed_permissions)


class ToolRegistry:
    def __init__(self, tools: list[ToolSpec] | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ToolSpec) -> None:
        if not tool.name.strip() or tool.handler is None:
            raise ValueError("tool name and handler are required")
        if tool.risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("unsupported tool risk")
        self._tools[tool.name] = tool

    def list_tools(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools[name] for name in sorted(self._tools))

    def discover(self, query: str, limit: int = 5) -> tuple[ToolSpec, ...]:
        if limit < 1:
            return ()
        terms = {x.lower() for x in query.split() if len(x) > 1}
        if not terms:
            return self.list_tools()[:limit]
        ranked = []
        for tool in self._tools.values():
            haystack = f"{tool.name} {tool.description} {tool.category}".lower()
            score = sum(term in haystack for term in terms)
            if score:
                ranked.append((score, tool))
        return tuple(t for _, t in sorted(ranked, key=lambda x: (-x[0], x[1].name))[:limit])

    def execute(self, name: str, arguments: Mapping[str, Any], policy: ToolPolicy) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"unknown tool: {name}")
        if not policy.allow(tool, arguments):
            raise PermissionError(f"tool execution denied: {name}")
        return tool.handler(**dict(arguments))  # type: ignore[misc]


@dataclass(frozen=True)
class AgentEvent:
    event_id: str
    kind: str
    payload: Mapping[str, Any]
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class SafePoint:
    name: str
    cancellable: bool = True
    preemptible: bool = True


class EventRuntime:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[AgentEvent], Any]]] = {}
        self._cancelled: set[str] = set()

    def on(self, kind: str, handler: Callable[[AgentEvent], Any]) -> None:
        self._handlers.setdefault(kind, []).append(handler)

    def cancel(self, event_id: str) -> None:
        self._cancelled.add(event_id)

    def dispatch(self, event: AgentEvent) -> list[Any]:
        if event.event_id in self._cancelled:
            return []
        return [handler(event) for handler in self._handlers.get(event.kind, ())]


@dataclass(frozen=True)
class LearningSignal:
    trace_id: str
    outcome: float
    process_rules: tuple[str, ...] = ()
    rubric: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class EvolutionCandidate:
    candidate_id: str
    carrier: str
    change: str
    evidence_trace_ids: tuple[str, ...]
    baseline_score: float
    candidate_score: float

    @property
    def improvement(self) -> float:
        return self.candidate_score - self.baseline_score


def validate_candidate(candidate: EvolutionCandidate, minimum_improvement: float = 0.0) -> bool:
    return candidate.improvement >= minimum_improvement and bool(candidate.evidence_trace_ids)


@dataclass(frozen=True)
class AgentNode:
    agent_id: str
    role: str
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class Handoff:
    source: str
    target: str
    task: str
    context_keys: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


@dataclass
class CollaborationGraph:
    agents: dict[str, AgentNode] = field(default_factory=dict)
    handoffs: list[Handoff] = field(default_factory=list)

    def add_agent(self, agent: AgentNode) -> None:
        if not agent.agent_id.strip() or agent.agent_id in self.agents:
            raise ValueError("agent_id is empty or already registered")
        self.agents[agent.agent_id] = agent

    def handoff(self, source: str, target: str, task: str, context_keys: tuple[str, ...] = (), evidence_ids: tuple[str, ...] = ()) -> Handoff:
        if source not in self.agents or target not in self.agents:
            raise KeyError("handoff participants must be registered agents")
        if source == target or not task.strip():
            raise ValueError("handoff must target a different agent and include a task")
        item = Handoff(source, target, task, context_keys, evidence_ids)
        self.handoffs.append(item)
        return item

    def topology(self) -> dict[str, Any]:
        return {"agents": [self.agents[k].__dict__ for k in sorted(self.agents)], "handoffs": [h.__dict__ for h in self.handoffs]}


__all__ = [
    "CAPABILITIES", "Capability", "CapabilityFabric", "ProviderAdapter", "ProviderAdapterRegistry",
    "MemoryRecord", "PersistentMemory", "ContextItem", "ContextPack", "build_context",
    "MemoryFact", "MemoryStore", "ToolSpec", "ToolPolicy", "AllowlistedToolPolicy", "ToolRegistry",
    "AgentEvent", "SafePoint", "EventRuntime", "LearningSignal", "EvolutionCandidate", "validate_candidate",
    "AgentNode", "Handoff", "CollaborationGraph",
]
