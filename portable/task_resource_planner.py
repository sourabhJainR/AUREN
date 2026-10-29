"""Task-aware memory and capability preflight for AUREN's canonical runtime.

This module composes existing owners. It does not create a second memory store,
provider catalog, scheduler, or execution authority. Plans are advisory receipts;
dispatch still passes through the existing Guard and execution controller.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping

from .memory_orchestrator import MemoryOrchestrator, MemoryBudgetError
from .agent_capabilities import CapabilityFabric
from .memory_efficiency import chunk_memory_context

_TASK_MEMORY_KINDS: dict[str, tuple[str, ...]] = {
    "implementation": ("working", "prospective", "episodic", "procedural", "semantic", "hierarchical"),
    "planning": ("working", "prospective", "episodic", "semantic", "hierarchical"),
    "research": ("working", "episodic", "semantic", "hierarchical", "reflective"),
    "review": ("working", "prospective", "episodic", "procedural", "semantic", "hierarchical", "reflective"),
    "security": ("working", "prospective", "episodic", "procedural", "semantic", "hierarchical"),
    "incident": ("working", "prospective", "episodic", "procedural", "semantic", "hierarchical"),
    "exploratory": ("working", "episodic", "semantic", "reflective", "sensory", "kv_cache"),
}


@dataclass(frozen=True, slots=True)
class TaskResourcePlan:
    task_class: str
    memory_kinds: tuple[str, ...]
    memory_ids: tuple[str, ...]
    required_memory_ids: tuple[str, ...]
    memory_chars_used: int
    memory_budget_chars: int
    omitted_memory_count: int
    chunks: tuple[Mapping[str, Any], ...]
    capabilities: tuple[Mapping[str, Any], ...]
    adaptive_strategies: tuple[str, ...]
    sampling_permitted: bool
    receipt_digest: str


def plan_task_resources(
    memory: MemoryOrchestrator,
    capabilities: CapabilityFabric,
    task: str,
    requested_capabilities: Iterable[str],
    *,
    task_class: str = "implementation",
    task_id: str | None = None,
    intent_digest: str | None = None,
    required_memory_ids: Iterable[str] = (),
    branch: str | None = None,
    source_revision: str | None = None,
    memory_budget_chars: int = 8_000,
    chunk_chars: int = 4_200,
    network_allowed: bool = False,
    sandbox_available: bool = True,
    max_risk: str = "high",
    allow_partial_exploration: bool = False,
) -> TaskResourcePlan:
    """Plan minimal task-scoped memory and gated capabilities with bounded handoffs.

    Protected tasks never use sampling or partial recall. The caller must still
    enforce approvals and policy at dispatch; this function only plans.
    """
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a non-empty string")
    if task_class not in _TASK_MEMORY_KINDS:
        raise ValueError("unsupported task_class")
    if type(memory_budget_chars) is not int or memory_budget_chars < 1:
        raise ValueError("memory_budget_chars must be a positive integer")
    if type(chunk_chars) is not int or chunk_chars < 128:
        raise ValueError("chunk_chars must be an integer >= 128")
    required = tuple(sorted({str(item).strip() for item in required_memory_ids if str(item).strip()}))
    partial_allowed = task_class == "exploratory" and allow_partial_exploration
    requested = tuple(dict.fromkeys(str(item).strip() for item in requested_capabilities if str(item).strip()))
    recall = memory.recall(
        task.strip(),
        kinds=_TASK_MEMORY_KINDS[task_class],
        task_id=task_id,
        intent_digest=intent_digest,
        budget_chars=memory_budget_chars,
        required_memory_ids=required,
        branch=branch,
        source_revision=source_revision,
    )
    # Non-required candidates may be omitted by ranked retrieval; expose the
    # count in the receipt. Required IDs remain fail-closed inside recall and
    # are checked again after chunking below.

    rows = [
        {
            "id": item.memory_id,
            "category": item.kind,
            "text": item.text,
            "verified": item.verified,
            "confidence": item.confidence,
            "created_at": item.created_at,
            "source_revision": item.source_revision,
            "intent_digest": item.intent_digest,
            "source": item.source,
            "evidence_ids": list(item.evidence_ids),
        }
        for item in recall.memories
    ]
    chunks = chunk_memory_context(rows, max_chars=chunk_chars)
    evidence_by_id = {str(row["id"]): tuple(row.get("evidence_ids", ())) for row in rows}
    emitted_ids = {record_id for chunk in chunks for record_id in chunk.record_ids}
    if set(required) - emitted_ids:
        raise MemoryBudgetError("required memory would be lost during bounded chunk assembly")

    selected_capabilities = capabilities.plan(
        requested,
        network_allowed=network_allowed,
        sandbox_available=sandbox_available,
        max_risk=max_risk,
    )
    cap_receipts = tuple({
        "name": item.name,
        "risk": item.risk,
        "requires_network": item.requires_network,
        "requires_sandbox": item.requires_sandbox,
        "fallback": item.fallback,
        "selected_reason": (
            "network fallback selected by canonical CapabilityFabric"
            if item.name not in set(requested) else
            "requested capability passed risk, network and sandbox gates"
        ),
    } for item in selected_capabilities)
    strategies = ["bounded_chunking", "fresh_context_handoff"]
    if len(selected_capabilities) > 1:
        strategies.append("bounded_batching")
    if len(chunks) > 1:
        strategies.append("stream_chunks")
    # Sampling is a declared option for explicitly exploratory work only; this
    # planner never samples on the caller's behalf or silently drops evidence.
    sampling_permitted = task_class == "exploratory" and allow_partial_exploration
    receipt_fields = {
        "task_class": task_class,
        "memory_kinds": list(_TASK_MEMORY_KINDS[task_class]),
        "memory_ids": sorted(item.memory_id for item in recall.memories),
        "required_memory_ids": list(required),
        "memory_chars_used": recall.chars_used,
        "memory_budget_chars": recall.budget_chars,
        "omitted_memory_count": recall.omitted,
        "chunks": [{"record_ids": list(chunk.record_ids), "chars": len(chunk.text)} for chunk in chunks],
        "capabilities": list(cap_receipts),
        "adaptive_strategies": strategies,
        "sampling_permitted": sampling_permitted,
    }
    digest = hashlib.sha256(
        json.dumps(receipt_fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return TaskResourcePlan(
        task_class=task_class,
        memory_kinds=_TASK_MEMORY_KINDS[task_class],
        memory_ids=tuple(sorted(item.memory_id for item in recall.memories)),
        required_memory_ids=required,
        memory_chars_used=recall.chars_used,
        memory_budget_chars=recall.budget_chars,
        omitted_memory_count=recall.omitted,
        chunks=tuple({
            "text": chunk.text,
            "record_ids": tuple(chunk.record_ids),
            "evidence_ids": tuple(sorted({evidence_id for record_id in chunk.record_ids for evidence_id in evidence_by_id.get(record_id, ())})),
            "index": chunk.index,
            "total": chunk.total,
        } for chunk in chunks),
        capabilities=cap_receipts,
        adaptive_strategies=tuple(strategies),
        sampling_permitted=sampling_permitted,
        receipt_digest=digest,
    )
