#!/usr/bin/env python3
"""Deterministic context planning for AI coding turns.

The planner turns task/risk/phase signals into a bounded retrieval plan. It does
not retrieve files itself; providers supply evidence candidates and this layer
ranks the evidence before prompt construction. Learned policy hints can tune
retrieval without bypassing explicit risk, security, or repository rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    evidence_id: str
    kind: str
    text: str
    relevance: float = 0.0
    confidence: float = 0.0
    freshness: float = 0.0
    cost: int = 1
    source: str = "unknown"

    def score(self) -> float:
        """Rank evidence while penalizing expensive context."""
        quality = (
            0.55 * max(0.0, min(1.0, self.relevance))
            + 0.20 * max(0.0, min(1.0, self.confidence))
            + 0.15 * max(0.0, min(1.0, self.freshness))
        )
        cost_penalty = min(0.10, max(0, self.cost) / 10000.0)
        return quality - cost_penalty


@dataclass(frozen=True, slots=True)
class ContextPlan:
    phase: str
    retrieval_modes: tuple[str, ...]
    budget: int
    max_items: int
    require_fresh_verification: bool
    policy_strategy: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalRecovery:
    """Bounded recovery decision for a failed context acquisition attempt."""

    action: str
    mode: str | None
    reason: str
    changed_strategy: bool
    terminal: bool = False


def plan_context(*, phase: str, risk: str = "medium", uncertainty: str = "medium",
                 policy_strategy: str | None = None) -> ContextPlan:
    """Select retrieval modes and a bounded budget for one agent phase."""
    phase = phase.lower().strip()
    risk = risk.lower().strip()
    uncertainty = uncertainty.lower().strip()

    modes: list[str] = ["instructions", "task_contract", "lexical"]
    if uncertainty in {"high", "unknown"} or phase in {"research", "investigate"}:
        modes += ["semantic", "history"]
    if phase in {"debug", "implement", "review", "verify"}:
        modes.append("structural")
    if phase in {"implement", "review", "verify"}:
        modes.append("memory")
    if risk in {"high", "critical"}:
        modes += ["security", "history"]

    required_modes = list(modes)
    strategy = (policy_strategy or "").strip().lower()
    if strategy in {"targeted_context", "structural_first"}:
        preferred = ["instructions", "task_contract", "structural", "lexical"]
        modes = preferred + [item for item in required_modes if item not in preferred]
    elif strategy in {"semantic_first", "research_first"}:
        modes = ["semantic"] + [item for item in required_modes if item != "semantic"]
    elif strategy in {"history_first", "regression_history"}:
        modes = ["history"] + [item for item in required_modes if item != "history"]
    else:
        modes = required_modes

    modes = list(dict.fromkeys(modes))
    budgets = {"low": 9000, "medium": 14000, "high": 20000, "critical": 24000}
    return ContextPlan(
        phase=phase,
        retrieval_modes=tuple(modes),
        budget=budgets.get(risk, budgets["medium"]),
        max_items=24 if risk in {"high", "critical"} else 18,
        require_fresh_verification=risk in {"high", "critical"} or phase == "verify",
        policy_strategy=policy_strategy,
    )


def choose_retrieval_recovery(
    *,
    failed_modes: Sequence[str],
    available_modes: Sequence[str],
    working_modes: Sequence[str] = (),
) -> RetrievalRecovery:
    """Choose a different retrieval path after a failed attempt.

    The ordering is deliberately deterministic. Historical working modes get
    first consideration, then the caller's available order. A failed mode can
    never be selected again during the same bounded acquisition. If no
    untried mode remains, the correct action is to stop rather than thrash.
    """
    failed = {str(mode).strip().lower() for mode in failed_modes if str(mode).strip()}
    available = tuple(dict.fromkeys(str(mode).strip().lower() for mode in available_modes if str(mode).strip()))
    preferred = tuple(dict.fromkeys(str(mode).strip().lower() for mode in working_modes if str(mode).strip()))
    candidates = tuple(dict.fromkeys((*preferred, *available)))

    for mode in candidates:
        if mode not in failed:
            return RetrievalRecovery(
                action="pivot",
                mode=mode,
                reason=f"retrieval path '{mode}' has not failed in this acquisition",
                changed_strategy=True,
            )

    return RetrievalRecovery(
        action="stop",
        mode=None,
        reason="all bounded retrieval paths have failed; no new evidence justifies another retry",
        changed_strategy=False,
        terminal=True,
    )


def select_evidence(candidates: Iterable[EvidenceCandidate], *, budget: int, max_items: int) -> list[EvidenceCandidate]:
    """Rank unique evidence records and fit them into a hard budget.

    Evidence IDs represent the same logical source. Deduplicate before scoring
    so a later duplicate cannot displace the first occurrence and accidentally
    change the selected evidence set merely because its metadata is different.
    """
    unique: list[EvidenceCandidate] = []
    seen: set[str] = set()
    for item in candidates:
        if item.evidence_id in seen:
            continue
        seen.add(item.evidence_id)
        unique.append(item)

    ranked = sorted(unique, key=lambda item: (item.score(), item.evidence_id), reverse=True)
    selected: list[EvidenceCandidate] = []
    used = 0
    for item in ranked:
        if item.cost < 0 or item.cost > budget - used:
            continue
        selected.append(item)
        used += item.cost
        if len(selected) >= max(1, int(max_items)):
            break
    return selected
