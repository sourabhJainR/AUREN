"""Unified, bounded memory orchestration for AUREN.

Coordinates existing owners instead of introducing a parallel memory database,
scheduler, or learning ledger. Durable content and provenance share the canonical
PersistentMemory SQLite file; working/sensory state is bounded and disposable;
the existing content-addressed context cache is reused.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .agent_capabilities import PersistentMemory, redact, sanitize_untrusted
from .context_graph import ContextGraph, ContextNode
from .context_engine import ContextItem

MEMORY_KINDS = (
    "episodic", "semantic", "working", "procedural", "sensory",
    "reflective", "hierarchical", "prospective", "meta_memory", "kv_cache",
)
EPHEMERAL_KINDS = frozenset({"working", "sensory"})
DURABLE_KINDS = frozenset(set(MEMORY_KINDS) - EPHEMERAL_KINDS - {"kv_cache", "meta_memory"})
DEFAULT_PRIORITY = {
    "working": 100, "prospective": 95, "episodic": 85, "procedural": 80,
    "semantic": 75, "hierarchical": 70, "reflective": 60, "sensory": 45,
    "meta_memory": 20, "kv_cache": 10,
}


class MemoryBudgetError(ValueError):
    """Raised when the requested context budget cannot preserve required content."""


@dataclass(frozen=True, slots=True)
class MemoryEnvelope:  # pragma: no cover - passive DTO; behavior is covered through the orchestrator
    memory_id: str
    kind: str
    text: str
    source: str
    confidence: float
    verified: bool
    evidence_ids: tuple[str, ...]
    intent_digest: str | None
    created_at: str
    expires_at: float | None = None


@dataclass(frozen=True, slots=True)
class MemoryWriteResult:  # pragma: no cover - passive DTO; behavior is covered through the orchestrator
    status: str
    kind: str
    memory_id: str | None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class MemoryRecall:  # pragma: no cover - passive DTO; behavior is covered through the orchestrator
    memories: tuple[MemoryEnvelope, ...]
    text: str
    chars_used: int
    budget_chars: int
    cache_hits: int
    cache_misses: int
    omitted: int


def _load_context_cache(root: Path) -> Any:
    """Load the existing harness cache by file path without a second cache implementation."""
    module_path = root / ".ai-harness" / "runtime" / "context_cache.py"
    if not module_path.is_file():
        return None
    module_name = "auren_existing_context_cache"
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
    return module.ContextPageCache()


class MemoryOrchestrator:
    """Route memory by lifetime and purpose, with bounded retrieval and explainable metrics.

    PersistentMemory is the only durable fact store. ContextGraph stores provenance
    in the same SQLite database. Existing task-memory, DreamMemory and
    ExecutionController integrations can be supplied as callbacks. No background
    worker is started, and this component never polls a provider.
    """

    def __init__(
        self,
        root: Path | str,
        project: str,
        *,
        memory: PersistentMemory | None = None,
        context_cache: Any | None = None,
        max_working_entries: int = 64,
        max_working_chars: int = 24_000,
        max_sensory_entries: int = 32,
        max_sensory_chars: int = 12_000,
        sensory_ttl_seconds: float = 300.0,
        owner_callbacks: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project must be a non-empty string")
        if min(max_working_entries, max_working_chars, max_sensory_entries, max_sensory_chars) < 1:
            raise ValueError("memory bounds must be positive")
        if sensory_ttl_seconds <= 0:
            raise ValueError("sensory_ttl_seconds must be positive")
        self.root = Path(root)
        self.project = project.strip()
        self.root.mkdir(parents=True, exist_ok=True)
        self.memory = memory or PersistentMemory(
            self.root / ".ai-harness" / "memory" / "auren-memory.sqlite3",
            require_approval=True,
        )
        self.graph = ContextGraph(self.memory, self.project)
        self.cache = context_cache if context_cache is not None else _load_context_cache(self.root)
        self.max_working_entries = max_working_entries
        self.max_working_chars = max_working_chars
        self.max_sensory_entries = max_sensory_entries
        self.max_sensory_chars = max_sensory_chars
        self.sensory_ttl_seconds = float(sensory_ttl_seconds)
        self.owner_callbacks = dict(owner_callbacks or {})
        self._working: dict[tuple[str, str, str], MemoryEnvelope] = {}
        self._sensory: dict[tuple[str, str, str], MemoryEnvelope] = {}
        self._lock = threading.RLock()
        self._metrics = {
            "writes": 0, "denied_writes": 0, "recalls": 0, "recall_hits": 0,
            "cache_hits": 0, "cache_misses": 0, "evictions": 0, "expired": 0,
            "budget_omissions": 0, "owner_callback_calls": 0, "durable_searches": 0,
        }

    @staticmethod
    def _validate_kind(kind: str) -> str:
        value = str(kind).strip().lower()
        if value not in MEMORY_KINDS:
            raise ValueError(f"unsupported memory kind: {kind}")
        return value

    @staticmethod
    def _terms(text: str) -> set[str]:
        return {term.casefold() for term in re.findall(r"[^\W_]+", text, flags=re.UNICODE) if len(term) > 1}

    def _ephemeral_envelope(
        self, *, kind: str, text: str, source: str, confidence: float,
        verified: bool, evidence_ids: Iterable[str], intent_digest: str | None,
        ttl_seconds: float | None,
    ) -> MemoryEnvelope:
        now = time.time()
        evidence = tuple(sorted({str(item).strip() for item in evidence_ids if str(item).strip()}))
        digest = hashlib.sha256(
            f"{kind}\0{self.project}\0{source}\0{intent_digest or ''}\0{text}".encode("utf-8")
        ).hexdigest()[:24]
        return MemoryEnvelope(
            digest, kind, text, source, confidence, verified, evidence, intent_digest,
            __import__("datetime").datetime.fromtimestamp(now, timezone.utc).isoformat(),
            now + ttl_seconds if ttl_seconds is not None else None,
        )

    def remember(
        self,
        kind: str,
        text: str,
        *,
        source: str,
        task_id: str | None = None,
        evidence_ids: Iterable[str] = (),
        intent_digest: str | None = None,
        confidence: float = 0.0,
        verified: bool = False,
        approved: bool = False,
        ttl_seconds: float | None = None,
        parent_memory_id: str | None = None,
    ) -> MemoryWriteResult:
        """Write to the owner for this memory kind; durable writes require approval."""
        kind = self._validate_kind(kind)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("memory text must be non-empty")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("memory source must be non-empty")
        clean_text = sanitize_untrusted(text.strip())
        clean_source = redact(source.strip())
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        evidence = tuple(sorted({str(item).strip() for item in evidence_ids if str(item).strip()}))
        if kind == "kv_cache":
            if self.cache is None:
                return MemoryWriteResult("unavailable", kind, None, "existing context cache module unavailable")
            page = self.cache.put("memory:kv_cache", clean_text, stable=bool(verified))
            self._metrics["writes"] += 1
            return MemoryWriteResult("cached", kind, page.page_id)

        if kind in EPHEMERAL_KINDS:
            char_limit = self.max_working_chars if kind == "working" else self.max_sensory_chars
            if len(clean_text) > char_limit:
                return MemoryWriteResult("budget_exceeded", kind, None, f"memory exceeds {char_limit}-character {kind} budget")
            scope = (str(task_id or "default"), kind)
            ttl = self.sensory_ttl_seconds if kind == "sensory" and ttl_seconds is None else ttl_seconds
            item = self._ephemeral_envelope(
                kind=kind, text=clean_text, source=clean_source, confidence=confidence,
                verified=verified, evidence_ids=evidence, intent_digest=intent_digest,
                ttl_seconds=ttl if kind == "sensory" or ttl_seconds is not None else None,
            )
            with self._lock:
                target = self._working if kind == "working" else self._sensory
                target[(scope[0], scope[1], item.memory_id)] = item
                self._trim_ephemeral(
                    target, self.max_working_entries if kind == "working" else self.max_sensory_entries,
                    self.max_working_chars if kind == "working" else self.max_sensory_chars,
                )
                self._metrics["writes"] += 1
            return MemoryWriteResult("stored_ephemeral", kind, item.memory_id)

        if kind == "meta_memory":
            # Meta-memory is an observable view of this coordinator, not another durable log.
            self._metrics["writes"] += 1
            return MemoryWriteResult("observed", kind, None, "use metrics() for current memory state")

        if not approved:
            self._metrics["denied_writes"] += 1
            return MemoryWriteResult("approval_required", kind, None, "durable memory writes require explicit approval")
        if parent_memory_id and self.graph.get_node(parent_memory_id) is None:
            raise KeyError(f"parent memory node not found: {parent_memory_id}")

        callback = self.owner_callbacks.get(kind)
        if callback is not None:
            self._metrics["owner_callback_calls"] += 1
            outcome = callback(
                project=self.project, text=clean_text, source=clean_source, task_id=task_id,
                evidence_ids=evidence, intent_digest=intent_digest, confidence=confidence,
                verified=verified, approved=approved, parent_memory_id=parent_memory_id,
            )
            self._metrics["writes"] += 1
            return MemoryWriteResult("delegated", kind, str(getattr(outcome, "id", "") or "") or None,
                                     "handled by registered canonical owner")

        record = self.memory.remember(
            self.project, kind, clean_text, intent_digest=intent_digest,
            confidence=confidence, verified=verified, approved=True,
        )
        if record is None:
            self._metrics["denied_writes"] += 1
            return MemoryWriteResult("approval_required", kind, None, "canonical memory owner rejected the write")
        node = ContextNode(
            node_id=record.id, kind=kind, label=record.text[:240], source=clean_source,
            confidence=confidence,
            properties={
                "evidence_ids": list(evidence), "intent_digest": intent_digest,
                "verified": bool(verified), "memory_kind": kind, "task_id": task_id,
            },
            created_at=record.created_at,
        )
        self.graph.upsert_node(node)
        if parent_memory_id:
            self.graph.link(parent_memory_id, "contains", record.id, source=clean_source, confidence=confidence,
                            properties={"memory_kind": kind, "task_id": task_id})
        self._metrics["writes"] += 1
        return MemoryWriteResult("stored", kind, record.id)

    def _trim_ephemeral(self, target: dict[tuple[str, str, str], MemoryEnvelope], max_entries: int,
                        max_chars: int | None) -> None:
        now = time.time()
        expired = [key for key, item in target.items() if item.expires_at is not None and item.expires_at <= now]
        for key in expired:
            del target[key]
            self._metrics["expired"] += 1
        while len(target) > max_entries or (max_chars is not None and sum(len(x.text) for x in target.values()) > max_chars):
            oldest = next(iter(target))
            del target[oldest]
            self._metrics["evictions"] += 1

    def _ephemeral_recall(self, kind: str, query: str, task_id: str | None, intent_digest: str | None = None) -> list[MemoryEnvelope]:
        now = time.time()
        target = self._working if kind == "working" else self._sensory
        scope = str(task_id or "default")
        terms = self._terms(query)
        with self._lock:
            expired = [key for key, item in target.items() if item.expires_at is not None and item.expires_at <= now]
            for key in expired:
                del target[key]
                self._metrics["expired"] += 1
            items = [
                item for (task, item_kind, _memory_id), item in target.items()
                if item_kind == kind and task == scope
                and (intent_digest is None or item.intent_digest == intent_digest)
                and (not terms or terms.intersection(self._terms(item.text)))
            ]
        items.sort(key=lambda item: (item.verified, item.confidence, item.created_at), reverse=True)
        return items

    def recall(
        self,
        query: str,
        *,
        kinds: Iterable[str] = ("working", "prospective", "episodic", "procedural", "semantic", "hierarchical", "reflective", "sensory"),
        task_id: str | None = None,
        intent_digest: str | None = None,
        budget_chars: int = 8_000,
        required_memory_ids: Iterable[str] = (),
    ) -> MemoryRecall:
        """Recall relevant memory under a hard character budget.

        Required IDs are fail-closed: if their complete text cannot fit, no partial
        context is returned. Provider calls are outside this component and never
        happen during recall or while the system is idle.
        """
        if budget_chars < 1:
            raise ValueError("budget_chars must be positive")
        before = self.cache.stats() if self.cache is not None else {"hits": 0, "misses": 0}
        kind_list = tuple(dict.fromkeys(self._validate_kind(kind) for kind in kinds))
        required_ids = set(required_memory_ids)
        candidates: list[MemoryEnvelope] = []
        durable_records = []
        if any(kind in DURABLE_KINDS for kind in kind_list):
            # One indexed query serves every requested durable category.
            durable_records = self.memory.search(
                self.project, query, intent_digest=intent_digest, limit=100,
            )
            self._metrics["durable_searches"] += 1
        for kind in kind_list:
            if kind in EPHEMERAL_KINDS:
                candidates.extend(self._ephemeral_recall(kind, query, task_id, intent_digest))
            elif kind == "kv_cache":
                if self.cache is not None:
                    for page in self.cache.search("memory:kv_cache", query, budget_chars=budget_chars):
                        candidates.append(MemoryEnvelope(
                            page.page_id, "kv_cache", page.text, "context-page-cache",
                            0.0, False, (), intent_digest,
                            datetime.now(timezone.utc).isoformat(),
                        ))
            elif kind in DURABLE_KINDS:
                for record in durable_records:
                    if record.category != kind:
                        continue
                    node = self.graph.get_node(record.id)
                    props = dict(node.properties or {}) if node else {}
                    candidates.append(MemoryEnvelope(
                        record.id, kind, record.text, node.source if node else "canonical-memory",
                        record.confidence, record.verified, tuple(props.get("evidence_ids", ())),
                        record.intent_digest, record.created_at,
                    ))
            elif kind == "meta_memory":
                continue
        unique = {item.memory_id: item for item in candidates}
        terms = self._terms(query)
        ranked = sorted(
            unique.values(),
            key=lambda item: (
                item.memory_id in required_ids, item.verified,
                len(terms.intersection(self._terms(item.text))),
                DEFAULT_PRIORITY.get(item.kind, 0), item.confidence, item.created_at,
            ),
            reverse=True,
        )
        selected: list[MemoryEnvelope] = []
        used = 0
        omitted = 0
        for item in ranked:
            size = len(item.text) + 1
            if item.memory_id in required_ids:
                if used + size > budget_chars:
                    raise MemoryBudgetError(
                        f"required memory {item.memory_id} does not fit budget; increase budget or explicitly revise contract"
                    )
                selected.append(item)
                used += size
            elif used + size <= budget_chars:
                selected.append(item)
                used += size
            else:
                omitted += 1
        if required_ids - {item.memory_id for item in selected}:
            raise MemoryBudgetError("one or more required memories were not found or did not fit")
        if self.cache is not None:
            for item in selected:
                if item.kind == "kv_cache":
                    continue  # a cache hit is read-only; do not duplicate its own page
                self.cache.put(f"memory:{item.kind}", item.text, stable=item.verified)
        # Rendering always uses the selected records so the cache cannot silently omit
        # a required item due to its own independent page-selection budget.
        rendered = "\n".join(item.text for item in selected)
        if len(rendered) > budget_chars:
            raise MemoryBudgetError("rendered memory exceeded hard context budget")
        after = self.cache.stats() if self.cache is not None else {"hits": 0, "misses": 0}
        cache_hits = int(after.get("hits", 0)) - int(before.get("hits", 0))
        cache_misses = int(after.get("misses", 0)) - int(before.get("misses", 0))
        self._metrics["recalls"] += 1
        self._metrics["recall_hits"] += len(selected)
        self._metrics["cache_hits"] += cache_hits
        self._metrics["cache_misses"] += cache_misses
        self._metrics["budget_omissions"] += omitted
        return MemoryRecall(tuple(selected), rendered, len(rendered), budget_chars, cache_hits, cache_misses, omitted)

    @staticmethod
    def context_items(recall: MemoryRecall) -> list[ContextItem]:
        """Adapt selected memories to the existing ContextEngine contract."""
        items: list[ContextItem] = []
        for memory in recall.memories:
            priority = DEFAULT_PRIORITY.get(memory.kind, 0) + int(memory.confidence * 10)
            refs = tuple(dict.fromkeys((memory.memory_id, *memory.evidence_ids)))
            items.append(ContextItem(
                kind=memory.kind, text=memory.text, source=memory.source,
                priority=priority, verified=memory.verified, refs=refs,
            ))
        return items

    def metrics(self) -> dict[str, int | float]:
        """Read-only, bounded telemetry; does not write a memory record or call an LLM."""
        with self._lock:
            result: dict[str, int | float] = dict(self._metrics)
            result["working_entries"] = len(self._working)
            result["working_chars"] = sum(len(item.text) for item in self._working.values())
            result["sensory_entries"] = len(self._sensory)
            result["sensory_chars"] = sum(len(item.text) for item in self._sensory.values())
            result["durable_kinds"] = len(DURABLE_KINDS)
            if self.cache is not None:
                result.update({f"cache_{key}": value for key, value in self.cache.stats().items()})
            return result

    def close(self) -> None:
        """Compatibility lifecycle hook; SQLite connections are operation-scoped."""
        return None
