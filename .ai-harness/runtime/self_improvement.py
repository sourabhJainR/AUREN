#!/usr/bin/env python3
"""Self-improvement primitives for AUREN.

AUREN learns from verified engineering outcomes, but learning is deliberately
separated from executable policy. This module turns outcomes into ranked,
explainable improvement proposals and only promotes proposals when they pass
reproducible evidence and regression gates supplied by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
import json
import time
from typing import Any, Callable, Iterable

VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class Outcome:
    task_id: str
    workflow: str
    success: bool
    accepted: bool
    verification_passed: bool
    regressions: int = 0
    retries: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    token_cost: int = 0
    latency_ms: int = 0
    failure_class: str = ""
    lesson: str = ""


@dataclass(frozen=True, slots=True)
class ImprovementProposal:
    id: str
    category: str
    change: str
    rationale: str
    evidence_task_ids: tuple[str, ...]
    confidence: float
    risk: str = "medium"
    executable: bool = False


def _stable_id(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "improve-" + sha256(raw.encode("utf-8")).hexdigest()[:16]


def summarize_outcomes(outcomes: Iterable[Outcome]) -> dict[str, Any]:
    rows = list(outcomes)
    if not rows:
        return {"count": 0, "accepted_rate": 0.0, "verification_rate": 0.0, "regression_rate": 0.0}
    n = len(rows)
    return {
        "count": n,
        "accepted_rate": sum(x.accepted for x in rows) / n,
        "verification_rate": sum(x.verification_passed for x in rows) / n,
        "regression_rate": sum(x.regressions > 0 for x in rows) / n,
        "avg_retries": sum(x.retries for x in rows) / n,
        "avg_model_calls": sum(x.model_calls for x in rows) / n,
        "avg_tool_calls": sum(x.tool_calls for x in rows) / n,
        "avg_token_cost": sum(x.token_cost for x in rows) / n,
        "avg_latency_ms": sum(x.latency_ms for x in rows) / n,
    }


def propose_improvements(outcomes: Iterable[Outcome], *, min_occurrences: int = 2) -> list[ImprovementProposal]:
    """Infer conservative improvement proposals from repeated outcome signals."""
    rows = list(outcomes)
    proposals: list[ImprovementProposal] = []

    failures: dict[str, list[Outcome]] = {}
    for row in rows:
        if row.failure_class:
            failures.setdefault(row.failure_class, []).append(row)
    for failure_class, evidence in failures.items():
        if len(evidence) < min_occurrences:
            continue
        ids = tuple(x.task_id for x in evidence)
        proposals.append(ImprovementProposal(
            id=_stable_id({"category": "failure", "class": failure_class, "ids": ids}),
            category="workflow_guardrail",
            change=f"Add a targeted guardrail or verification step for failure class '{failure_class}'.",
            rationale=f"The same failure class occurred {len(evidence)} times in verified task outcomes.",
            evidence_task_ids=ids,
            confidence=min(0.95, 0.55 + 0.10 * len(evidence)),
            risk="medium",
        ))

    retry_rows = [x for x in rows if x.retries >= 3 and x.success]
    if len(retry_rows) >= min_occurrences:
        ids = tuple(x.task_id for x in retry_rows)
        proposals.append(ImprovementProposal(
            id=_stable_id({"category": "thrash", "ids": ids}),
            category="context_or_routing",
            change="Add an evidence-gathering or strategy-change trigger before repeated retries.",
            rationale="Successful tasks repeatedly required three or more retries, indicating avoidable search/edit thrash.",
            evidence_task_ids=ids,
            confidence=min(0.90, 0.50 + 0.10 * len(retry_rows)),
            risk="low",
        ))

    expensive = [x for x in rows if x.success and x.accepted and (x.model_calls >= 8 or x.tool_calls >= 15)]
    if len(expensive) >= min_occurrences:
        ids = tuple(x.task_id for x in expensive)
        proposals.append(ImprovementProposal(
            id=_stable_id({"category": "efficiency", "ids": ids}),
            category="context_efficiency",
            change="Prefer stronger pre-retrieval filtering and evidence reuse for this workflow class.",
            rationale="Accepted tasks repeatedly consumed high model/tool call volume despite successful outcomes.",
            evidence_task_ids=ids,
            confidence=min(0.90, 0.50 + 0.08 * len(expensive)),
            risk="low",
        ))

    return sorted(proposals, key=lambda p: (p.confidence, p.id), reverse=True)


def evaluate_proposal(
    proposal: ImprovementProposal,
    *,
    regression_gate: Callable[[ImprovementProposal], bool],
    safety_gate: Callable[[ImprovementProposal], bool],
) -> ImprovementProposal:
    """Promote a proposal only when both regression and safety gates pass."""
    if not regression_gate(proposal) or not safety_gate(proposal):
        return proposal
    return ImprovementProposal(**{**asdict(proposal), "executable": True})


def learning_record(*, proposal: ImprovementProposal, promoted: bool, evaluated_at: int | None = None) -> dict[str, Any]:
    """Return an append-friendly audit record for the learning ledger."""
    return {
        "version": VERSION,
        "type": "improvement_evaluation",
        "id": proposal.id,
        "category": proposal.category,
        "change": proposal.change,
        "rationale": proposal.rationale,
        "evidence_task_ids": list(proposal.evidence_task_ids),
        "confidence": proposal.confidence,
        "risk": proposal.risk,
        "promoted": promoted,
        "evaluated_at": int(time.time()) if evaluated_at is None else int(evaluated_at),
    }
