"""Task-aware memory and capability routing over AUREN's canonical owners.

This module is deliberately provider-neutral. It does not create stores, install
extensions, execute discovered resources, or grant permissions. Callers pass the
canonical MemoryOrchestrator and already-discovered capability metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from .memory_orchestrator import MEMORY_KINDS, MemoryOrchestrator, MemoryRecall


_TASK_KINDS: dict[str, tuple[str, ...]] = {
    "conversation": ("working", "episodic", "semantic"),
    "coding": ("working", "episodic", "procedural", "semantic", "hierarchical", "prospective"),
    "debugging": ("working", "episodic", "procedural", "semantic", "hierarchical"),
    "research": ("working", "episodic", "semantic", "hierarchical"),
    "planning": ("working", "prospective", "episodic", "procedural", "semantic", "hierarchical"),
    "decision": ("working", "semantic", "episodic", "procedural"),
    "operations": ("working", "prospective", "episodic", "procedural", "semantic"),
    "review": ("working", "episodic", "procedural", "semantic", "hierarchical"),
}
_TASK_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("debugging", ("debug", "debugging", "bug", "failure", "traceback", "incident", "regression", "crash")),
    ("coding", ("implement", "implementation", "refactor", "code", "function", "class", "compile", "feature", "patch")),
    ("review", ("review", "audit", "verify", "quality gate", "security check", "regression test")),
    ("research", ("research", "investigate", "compare", "look up", "sources", "benchmark")),
    ("planning", ("plan", "roadmap", "sequence", "break down", "milestone", "delegate")),
    ("operations", ("deploy", "release", "rollback", "migration", "incident response", "runbook")),
    ("decision", ("should i", "choose", "recommend", "trade-off", "tradeoff", "decide")),
)


@dataclass(frozen=True, slots=True)
class MemoryPlan:
    task_type: str
    kinds: tuple[str, ...]
    budget_chars: int
    task_id: str | None = None
    intent_digest: str | None = None
    branch: str | None = None
    source_revision: str | None = None
    required_memory_ids: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ResourceCandidate:
    """Read-only advertised metadata; it is not an execution grant."""
    name: str
    source: str
    capabilities: frozenset[str] = frozenset()
    available: bool = True
    permission_granted: bool = False
    privacy_compatible: bool = False
    risk: str = "high"
    network_required: bool = False
    network_allowed: bool = False
    sandbox_required: bool = False
    sandbox_available: bool = False
    estimated_cost: float | None = None
    estimated_latency_ms: int | None = None
    measured_quality: float | None = None
    metadata_version: str | None = None
    failure_rate: float | None = None
    tags: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ResourceDecision:
    selected: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]
    considered: int
    policy_digest: str


@dataclass(frozen=True, slots=True)
class RoutingReceipt:
    task_type: str
    query_digest: str
    memory_kinds: tuple[str, ...]
    memory_ids: tuple[str, ...]
    memory_omitted: int
    budget_chars: int
    chars_used: int
    resource_names: tuple[str, ...] = ()
    rejected_resources: tuple[tuple[str, str], ...] = ()
    policy_digest: str = ""


def classify_task(task: str, explicit_type: str | None = None) -> tuple[str, str]:
    """Classify deterministically; explicit valid caller intent always wins."""
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a non-empty string")
    if explicit_type is not None:
        kind = explicit_type.strip().lower()
        if kind not in _TASK_KINDS:
            raise ValueError(f"unsupported task type: {explicit_type}")
        return kind, "explicit task type"
    normalized = task.casefold()
    for kind, markers in _TASK_MARKERS:
        if any(re.search(r"(?<!\w)" + re.escape(marker) + r"(?!\w)", normalized) for marker in markers):
            return kind, f"matched {kind} task markers"
    return "conversation", "safe default for unclassified task"


def plan_memory(
    task: str,
    *,
    task_type: str | None = None,
    budget_chars: int = 8_000,
    task_id: str | None = None,
    intent_digest: str | None = None,
    branch: str | None = None,
    source_revision: str | None = None,
    required_memory_ids: Iterable[str] = (),
    include_sensory: bool = False,
    include_reflective: bool = False,
) -> MemoryPlan:
    if type(budget_chars) is not int or budget_chars < 1:
        raise ValueError("budget_chars must be a positive integer")
    resolved, reason = classify_task(task, task_type)
    kinds = list(_TASK_KINDS[resolved])
    if include_sensory:
        kinds.append("sensory")
    if include_reflective:
        kinds.append("reflective")
    required = tuple(dict.fromkeys(str(item).strip() for item in required_memory_ids if str(item).strip()))
    if required:
        # Required IDs may belong to a different memory kind than the task default.
        # Search all kinds so required context is resolved or fails closed, never hidden by routing.
        kinds.extend(MEMORY_KINDS)
    # Stable deduplication and explicit validation protect the recall boundary.
    selected = tuple(dict.fromkeys(kinds))
    if any(kind not in MEMORY_KINDS for kind in selected):
        raise ValueError("memory plan contains an unsupported memory kind")
    return MemoryPlan(
        task_type=resolved, kinds=selected, budget_chars=budget_chars, task_id=task_id,
        intent_digest=intent_digest, branch=branch, source_revision=source_revision,
        required_memory_ids=required, reason=reason,
    )


def recall_for_task(orchestrator: MemoryOrchestrator, task: str, **options: Any) -> tuple[MemoryPlan, MemoryRecall]:
    """Use the canonical orchestrator; required context remains fail-closed."""
    allowed = {
        "task_type", "budget_chars", "task_id", "intent_digest", "branch",
        "source_revision", "required_memory_ids", "include_sensory", "include_reflective",
    }
    unknown = set(options) - allowed
    if unknown:
        raise TypeError(f"unsupported memory routing options: {sorted(unknown)}")
    plan = plan_memory(task, **options)
    recall = orchestrator.recall(
        task, kinds=plan.kinds, task_id=plan.task_id, intent_digest=plan.intent_digest,
        budget_chars=plan.budget_chars, required_memory_ids=plan.required_memory_ids,
        branch=plan.branch, source_revision=plan.source_revision,
    )
    return plan, recall


def select_resources(
    candidates: Sequence[ResourceCandidate],
    *,
    required_capabilities: Iterable[str],
    max_risk: str = "medium",
    max_cost: float | None = None,
    max_latency_ms: int | None = None,
    max_candidates: int = 3,
    require_measured_quality: bool = False,
    policy_context: Mapping[str, Any] | None = None,
) -> ResourceDecision:
    """Rank only eligible advertised resources; never execute or authorize them."""
    risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    if max_risk not in risk_order:
        raise ValueError("invalid max_risk")
    if max_cost is not None and (
        isinstance(max_cost, bool) or not isinstance(max_cost, (int, float))
        or not math.isfinite(max_cost) or max_cost < 0
    ):
        raise ValueError("max_cost must be a finite non-negative number")
    if max_latency_ms is not None and (
        type(max_latency_ms) is not int or max_latency_ms < 0
    ):
        raise ValueError("max_latency_ms must be a non-negative integer")
    if type(max_candidates) is not int or max_candidates < 1:
        raise ValueError("max_candidates must be a positive integer")
    required = frozenset(str(item).strip().lower() for item in required_capabilities if str(item).strip())
    policy = {
        "required": sorted(required), "max_risk": max_risk, "max_cost": max_cost,
        "max_latency_ms": max_latency_ms, "max_candidates": max_candidates,
        "require_measured_quality": require_measured_quality, "context": dict(policy_context or {}),
    }
    policy_digest = hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    eligible: list[tuple[tuple[float, ...], ResourceCandidate]] = []
    rejected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, ResourceCandidate):
            rejected.append(("<invalid-candidate>", "invalid candidate descriptor type"))
            continue
        name = candidate.name if isinstance(candidate.name, str) else "<invalid-candidate>"
        if name in seen:
            rejected.append((name, "duplicate candidate name"))
            continue
        seen.add(name)
        reason = ""
        flags = (
            candidate.available, candidate.permission_granted, candidate.privacy_compatible,
            candidate.network_required, candidate.network_allowed,
            candidate.sandbox_required, candidate.sandbox_available,
        )
        if not isinstance(candidate.name, str) or not candidate.name.strip() or not isinstance(candidate.source, str) or not candidate.source.strip():
            reason = "missing identity or source"
        elif any(type(flag) is not bool for flag in flags):
            reason = "invalid boolean policy metadata"
        elif not candidate.available:
            reason = "unavailable"
        elif candidate.permission_granted is not True:
            reason = "permission not granted"
        elif candidate.privacy_compatible is not True:
            reason = "privacy constraints not satisfied"
        elif not isinstance(candidate.risk, str) or candidate.risk not in risk_order:
            reason = "invalid risk metadata"
        elif risk_order[candidate.risk] > risk_order[max_risk]:
            reason = "risk exceeds policy"
        elif candidate.network_required and not candidate.network_allowed:
            reason = "network not allowed"
        elif candidate.sandbox_required and not candidate.sandbox_available:
            reason = "required sandbox unavailable"
        elif not isinstance(candidate.capabilities, (set, frozenset, tuple, list)) or not required.issubset({str(x).lower() for x in candidate.capabilities}):
            reason = "required capabilities missing"
        elif candidate.estimated_cost is not None and (
            isinstance(candidate.estimated_cost, bool) or not isinstance(candidate.estimated_cost, (int, float))
            or not math.isfinite(candidate.estimated_cost) or candidate.estimated_cost < 0
        ):
            reason = "invalid cost measurement"
        elif candidate.estimated_latency_ms is not None and (
            type(candidate.estimated_latency_ms) is not int or candidate.estimated_latency_ms < 0
        ):
            reason = "invalid latency measurement"
        elif max_cost is not None and (candidate.estimated_cost is None or candidate.estimated_cost > max_cost):
            reason = "cost unknown or exceeds budget"
        elif max_latency_ms is not None and (candidate.estimated_latency_ms is None or candidate.estimated_latency_ms > max_latency_ms):
            reason = "latency unknown or exceeds budget"
        elif require_measured_quality and candidate.measured_quality is None:
            reason = "measured quality unavailable"
        elif candidate.measured_quality is not None and (
            isinstance(candidate.measured_quality, bool) or not isinstance(candidate.measured_quality, (int, float))
            or not math.isfinite(candidate.measured_quality) or not 0 <= candidate.measured_quality <= 1
        ):
            reason = "invalid quality measurement"
        elif candidate.failure_rate is not None and (
            isinstance(candidate.failure_rate, bool) or not isinstance(candidate.failure_rate, (int, float))
            or not math.isfinite(candidate.failure_rate) or not 0 <= candidate.failure_rate <= 1
        ):
            reason = "invalid failure-rate measurement"
        if reason:
            rejected.append((candidate.name, reason))
            continue
        # Missing measurements are neutral, never treated as proof of quality.
        quality = candidate.measured_quality if candidate.measured_quality is not None else 0.0
        failure = candidate.failure_rate if candidate.failure_rate is not None else 1.0
        latency = candidate.estimated_latency_ms if candidate.estimated_latency_ms is not None else 2**31
        cost = candidate.estimated_cost if candidate.estimated_cost is not None else float("inf")
        eligible.append(((-quality, failure, cost, latency, candidate.name), candidate))
    eligible.sort(key=lambda pair: pair[0])
    chosen = tuple(item.name for _, item in eligible[:max_candidates])
    chosen_set = set(chosen)
    rejected.extend((item.name, "lower-ranked eligible candidate") for _, item in eligible if item.name not in chosen_set)
    return ResourceDecision(chosen, tuple(rejected), len(candidates), policy_digest)


def make_receipt(task: str, plan: MemoryPlan, recall: MemoryRecall,
                 resources: ResourceDecision | None = None) -> RoutingReceipt:
    """Create a compact, non-secret routing record; never includes prompts or memory text."""
    digest = hashlib.sha256(task.encode("utf-8")).hexdigest()
    return RoutingReceipt(
        task_type=plan.task_type, query_digest=digest, memory_kinds=plan.kinds,
        memory_ids=tuple(item.memory_id for item in recall.memories),
        memory_omitted=recall.omitted, budget_chars=recall.budget_chars, chars_used=recall.chars_used,
        resource_names=resources.selected if resources else (),
        rejected_resources=resources.rejected if resources else (),
        policy_digest=resources.policy_digest if resources else "",
    )
