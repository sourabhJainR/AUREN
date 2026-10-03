"""Evidence-driven execution strategy selection.

This layer turns durable evidence lineage into bounded planning inputs. It is
decision-only: it never executes tools, changes capabilities, or promotes a
lifecycle state. Evidence with contamination, missing verification, or
conflicting outcomes is excluded or causes a safe fallback rather than being
silently averaged into a decision.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
from statistics import mean
from typing import Any, Mapping, Sequence

from .persistent_evidence_graph import EvidenceNode, PersistentEvidenceGraph
from .evidence_freshness_policy import EvidenceFreshnessPolicy
from .continuous_engineering_decision_fabric import (
    ContinuousEngineeringDecisionFabric,
    Decision,
    DecisionCandidate,
    RepositoryContext,
)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class EvidenceDecisionSignal:
    strategy: str
    observations: int
    success_rate: float
    quality: float
    duration: float
    cost: float
    confidence: float
    conflicted: bool = False
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceDecisionPlan:
    task_family: str
    capability: str
    selected: DecisionCandidate
    verification_depth: str
    retry_budget: int
    escalation: str
    parallel: bool
    resource_lane: str
    duration_budget: float
    memory_budget_mb: int
    confidence: float
    evidence_ids: tuple[str, ...]
    fallback: bool
    rationale: tuple[str, ...]
    decision_digest: str


class EvidenceDrivenDecisionFabric:
    """Use persistent evidence lineage to adapt execution strategy safely."""

    def __init__(
        self,
        graph: PersistentEvidenceGraph,
        *,
        decision_fabric: ContinuousEngineeringDecisionFabric | None = None,
    ) -> None:
        if not isinstance(graph, PersistentEvidenceGraph):
            raise TypeError("graph must be PersistentEvidenceGraph")
        self.graph = graph
        self.decision_fabric = decision_fabric

    @staticmethod
    def _metadata(node: EvidenceNode) -> dict[str, str]:
        return dict(node.metadata)

    def _signals(
        self,
        capability: str,
        candidates: Sequence[Mapping[str, Any]],
        *,
        task_family: str = "",
        freshness_policy: EvidenceFreshnessPolicy | None = None,
        now: datetime | None = None,
        max_edges: int = 200,
    ) -> tuple[EvidenceDecisionSignal, ...]:
        freshness = freshness_policy or EvidenceFreshnessPolicy()
        current = now or datetime.now(timezone.utc)
        root = self.graph.add_node("capability", capability)
        edges = self.graph.lineage(root.node_id, direction="both", max_edges=max_edges)
        node_ids = {
            edge.source_id if edge.source_id != root.node_id else edge.target_id
            for edge in edges
        }
        rows: dict[str, list[tuple[float, float, float, float, str]]] = {}
        candidate_keys = {
            (str(c["provider"]), str(c.get("tool_path", c["provider"])))
            for c in candidates
        }
        for node_id in sorted(node_ids):
            node = self.graph.get_node(node_id)
            if node is None:
                continue
            md = self._metadata(node)
            if md.get("kind") != "decision-observation":
                continue
            if md.get("capability") != capability:
                continue
            if md.get("task_family") and md.get("task_family") != task_family:
                continue
            if md.get("contaminated", "false").lower() == "true":
                continue
            if md.get("verified", "false").lower() != "true":
                continue
            if freshness.weight(md.get("observed_at"), now=current) <= 0:
                continue
            key = (md.get("provider", ""), md.get("tool_path", ""))
            if key not in candidate_keys:
                continue
            try:
                success = float(md["success"])
                quality = float(md["quality"])
                duration = float(md["duration"])
                cost = float(md["cost"])
            except (KeyError, ValueError):
                continue
            if not (0 <= success <= 1 and 0 <= quality <= 1 and duration >= 0 and cost >= 0):
                continue
            rows.setdefault("|".join(key), []).append(
                (success, quality, duration, cost, node.node_id)
            )

        signals = []
        for key in sorted(rows):
            values = rows[key]
            strategies = key.split("|", 1)
            successes = mean(v[0] for v in values)
            quality = mean(v[1] for v in values)
            duration = mean(v[2] for v in values)
            cost = mean(v[3] for v in values)
            confidence = min(1.0, len(values) / 10.0)
            conflicted = len(values) >= 2 and (
                max(v[0] for v in values) - min(v[0] for v in values) > .5
                or max(v[1] for v in values) - min(v[1] for v in values) > .5
            )
            signals.append(
                EvidenceDecisionSignal(
                    f"{strategies[0]}|{strategies[1]}",
                    len(values),
                    round(successes, 4),
                    round(quality, 4),
                    round(duration, 4),
                    round(cost, 4),
                    round(confidence, 4),
                    conflicted,
                    tuple(v[4] for v in values),
                )
            )
        return tuple(signals)

    def plan(
        self,
        task_family: str,
        capability: str,
        candidates: Sequence[Mapping[str, Any]],
        *,
        duration_budget: float = 3600.0,
        memory_budget_mb: int = 4096,
        max_retries: int = 2,
        repository: RepositoryContext | None = None,
        min_confidence: float = 0.3,
        freshness_policy: EvidenceFreshnessPolicy | None = None,
        now: datetime | None = None,
    ) -> EvidenceDecisionPlan:
        if not task_family.strip() or not capability.strip():
            raise ValueError("task_family and capability are required")
        if not candidates:
            raise ValueError("at least one decision candidate is required")
        if duration_budget <= 0 or memory_budget_mb < 1 or max_retries < 0:
            raise ValueError("invalid resource or retry budget")
        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")

        signals = self._signals(capability, candidates, task_family=task_family, freshness_policy=freshness_policy, now=now)
        by_strategy = {s.strategy: s for s in signals}
        decision: Decision | None = None
        if self.decision_fabric is not None:
            decision = self.decision_fabric.decide(
                task_family, capability, candidates, repository=repository
            )

        selected = None
        selected_signal = None
        for candidate in (decision.selected,) if decision else ():
            key = f"{candidate.provider}|{candidate.tool_path}"
            if key in by_strategy:
                selected, selected_signal = candidate, by_strategy[key]
                break

        if selected is None:
            # Evidence must beat the cold-start heuristic when it is strong
            # enough. Otherwise retain a conservative standard-verification
            # candidate and explicitly mark the plan as a fallback.
            eligible = [c for c in candidates if f"{c['provider']}|{c.get('tool_path', c['provider'])}" in by_strategy]
            if eligible:
                eligible.sort(
                    key=lambda c: (
                        -by_strategy[f"{c['provider']}|{c.get('tool_path', c['provider'])}"].confidence,
                        -by_strategy[f"{c['provider']}|{c.get('tool_path', c['provider'])}"].quality,
                        by_strategy[f"{c['provider']}|{c.get('tool_path', c['provider'])}"].duration,
                        str(c["provider"]),
                    )
                )
                key = f"{eligible[0]['provider']}|{eligible[0].get('tool_path', eligible[0]['provider'])}"
                selected_signal = by_strategy[key]
                raw = eligible[0]
                selected = DecisionCandidate(
                    str(raw["provider"]),
                    str(raw.get("tool_path", raw["provider"])),
                    str(raw.get("verification_depth", "standard")),
                    bool(raw.get("parallel", False)),
                    round(selected_signal.duration, 4),
                    round(1 - selected_signal.success_rate, 4),
                    round(selected_signal.quality, 4),
                    round(selected_signal.cost, 4),
                    round(selected_signal.quality * 100 - (1 - selected_signal.success_rate) * 45 - selected_signal.duration * .08 - selected_signal.cost * 2, 4),
                    ("persistent verified evidence selected the route",),
                )

        fallback = selected is None or selected_signal is None or selected_signal.confidence < min_confidence or selected_signal.conflicted
        if fallback:
            raw = candidates[0]
            selected = DecisionCandidate(
                str(raw["provider"]),
                str(raw.get("tool_path", raw["provider"])),
                "deep",
                False,
                float(raw.get("expected", {}).get("duration", 600.0)),
                float(raw.get("expected", {}).get("failure", 0.5)),
                float(raw.get("expected", {}).get("quality", 0.5)),
                float(raw.get("expected", {}).get("cost", 0.0)),
                0.0,
                ("insufficient trusted evidence; conservative fallback",),
            )
            confidence = 0.0 if selected_signal is None else selected_signal.confidence
            evidence_ids = () if selected_signal is None else selected_signal.evidence_ids
        else:
            confidence = selected_signal.confidence
            evidence_ids = selected_signal.evidence_ids

        verification = "deep" if fallback or selected.expected_failure >= .25 else (
            "independent" if confidence >= .7 and selected.expected_failure < .15 else "standard"
        )
        retries = max_retries if fallback else (max_retries if selected.expected_failure >= .2 else max(0, max_retries - 1))
        escalation = "human-review" if fallback or selected.expected_failure >= .4 else (
            "alternate-provider" if selected.expected_failure >= .2 else "none"
        )
        parallel = bool(selected.parallel) and not fallback
        lane = "local" if selected.provider == "local" else "agent"
        if selected.expected_duration > duration_budget:
            raise ValueError("selected strategy exceeds duration budget")
        if int(memory_budget_mb) < 256:
            raise ValueError("memory budget too small for safe execution")

        rationale = (
            "persistent evidence is filtered to verified, uncontaminated observations",
            f"trusted evidence confidence={confidence:.2f}",
            f"verification depth={verification}",
            f"retry budget={retries}; escalation={escalation}",
            f"resource lane={lane}",
        )
        payload = {
            "task_family": task_family,
            "capability": capability,
            "selected": asdict(selected),
            "verification_depth": verification,
            "retry_budget": retries,
            "escalation": escalation,
            "parallel": parallel,
            "resource_lane": lane,
            "duration_budget": duration_budget,
            "memory_budget_mb": memory_budget_mb,
            "confidence": round(confidence, 4),
            "evidence_ids": evidence_ids,
            "fallback": fallback,
            "rationale": rationale,
        }
        return EvidenceDecisionPlan(
            task_family, capability, selected, verification, retries, escalation,
            parallel, lane, duration_budget, memory_budget_mb, round(confidence, 4),
            evidence_ids, fallback, rationale, _digest(payload)
        )


__all__ = ["EvidenceDecisionSignal", "EvidenceDecisionPlan", "EvidenceDrivenDecisionFabric"]
