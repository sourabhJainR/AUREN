"""Bounded, deterministic resource adaptation for graph/crew context.

This module changes representation under pressure; it never changes authorization,
risk classification, verification, or promotion policy.
"""
from __future__ import annotations

from typing import Iterable

_CRITICAL_MARKERS = (
    "acceptance", "must not", "must never", "blocked", "fail closed",
    "security", "rollback", "evidence", "required", "denied", "failure",
)


def iter_text_chunks(text: str, chunk_chars: int = 4096) -> Iterable[str]:
    """Yield bounded chunks without allocating a second full-size copy."""
    if chunk_chars < 1:
        raise ValueError("chunk_chars must be positive")
    for start in range(0, len(text), chunk_chars):
        yield text[start:start + chunk_chars]


def bounded_text(text: str, limit: int, *, chunk_chars: int = 4096) -> tuple[str, str]:
    """Return text within a hard character limit, preserving critical lines and both edges.

    The result explicitly marks omitted content. This is lossy context compression,
    not a substitute for the original artifact or evidence record.
    """
    if limit < 1:
        raise ValueError("limit must be positive")
    if len(text) <= limit:
        return text, "complete"
    marker = "\n...[adaptive resource handling: omitted content; original remains authoritative]...\n"
    if limit <= len(marker) + 8:
        return text[:limit], "bounded_prefix"
    lines = text.splitlines()
    critical = []
    seen = set()
    for line in lines:
        if any(term in line.lower() for term in _CRITICAL_MARKERS):
            candidate = line.strip()
            if candidate and candidate not in seen:
                critical.append(candidate)
                seen.add(candidate)
    budget = limit - len(marker)
    # Reserve roughly equal space for the beginning and end. Critical lines fill
    # only the remaining budget and are selected in source order for determinism.
    edge_budget = max(4, budget // 3)
    head = text[:edge_budget]
    tail = text[-edge_budget:]
    middle_budget = max(0, budget - len(head) - len(tail))
    kept = []
    used = 0
    for line in critical:
        cost = len(line) + 1
        if used + cost > middle_budget:
            break
        kept.append(line)
        used += cost
    sample = "\n".join(kept)
    result = head + marker + (sample + "\n" if sample else "") + tail
    if len(result) > limit:
        result = result[:limit]
    return result, "head_tail_critical_sample"


def sample_items(items: Iterable[str], limit: int) -> list[str]:
    """Deterministically retain the first and last items under a sample cap."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    values = list(items)
    if len(values) <= limit:
        return values
    if limit == 0:
        return []
    if limit == 1:
        return [values[0]]
    indices = {round(i * (len(values) - 1) / (limit - 1)) for i in range(limit)}
    return [values[i] for i in sorted(indices)]


def adaptive_context_limit(base_limit: int, pressure: dict[str, float]) -> int:
    """Reduce optional context under measured CPU/memory/queue pressure.

    Thresholds are deterministic policy knobs, not learned claims. Learning may
    tune them only after paired task-success and critical-evidence evaluations.
    """
    if base_limit < 1:
        raise ValueError("base_limit must be positive")
    observed = []
    for key in ("cpu_pressure", "memory_pressure", "queue_pressure"):
        value = pressure.get(key)
        if isinstance(value, (int, float)):
            observed.append(max(0.0, min(1.0, float(value))))
    peak = max(observed, default=0.0)
    if peak >= 0.85:
        return max(1, base_limit // 2)
    if peak >= 0.70:
        return max(1, (base_limit * 3) // 4)
    return base_limit
