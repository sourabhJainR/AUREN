"""Structured, conservative evidence attribution for collaborative skill execution.

This module deliberately treats attribution as an evidence signal, not proof of
causality. It records per-member observations and compares a bundle with the
best known singleton evidence when available.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

_FINDING = re.compile(r"(?i)\b(finding|findings|defect|bug|regression|issue|risk|failure|warning|gap)\b")
_EVIDENCE = re.compile(r"(?i)\b(evidence|verified|verification|test|tests|assert|proof|reproduced|reproduction)\b")


@dataclass(frozen=True)
class SkillExecutionEvidence:
    skill: str
    source: str
    phase: str
    group: int
    status: str
    evidence_quality: float
    unique_findings: int
    evidence_signals: int
    contribution: float
    redundant: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "source": self.source,
            "phase": self.phase,
            "group": self.group,
            "status": self.status,
            "evidence_quality": round(self.evidence_quality, 3),
            "unique_findings": self.unique_findings,
            "evidence_signals": self.evidence_signals,
            "contribution": round(self.contribution, 3),
            "redundant": self.redundant,
        }


@dataclass(frozen=True)
class CollaborationAssessment:
    bundle_id: str
    bundle_quality: float
    singleton_baseline: float | None
    collaboration_delta: float
    incremental_cost: float
    incremental_latency_seconds: float
    promotable: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "bundle_quality": round(self.bundle_quality, 3),
            "singleton_baseline": None if self.singleton_baseline is None else round(self.singleton_baseline, 3),
            "collaboration_delta": round(self.collaboration_delta, 3),
            "incremental_cost": round(self.incremental_cost, 3),
            "incremental_latency_seconds": round(self.incremental_latency_seconds, 3),
            "promotable": self.promotable,
            "reason": self.reason,
        }


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _signals(output: str) -> tuple[int, int]:
    lines = [line.strip() for line in str(output).splitlines() if line.strip()]
    findings = sum(1 for line in lines if _FINDING.search(line))
    evidence = sum(1 for line in lines if _EVIDENCE.search(line))
    return min(20, findings), min(20, evidence)


def attribute(
    *,
    members: Sequence[Mapping[str, Any]],
    execution_groups: Sequence[Sequence[str]],
    output: str,
    status: str,
    evidence_quality: float,
    role: str,
) -> tuple[SkillExecutionEvidence, ...]:
    """Assign conservative, deterministic contribution signals to selected skills.

    Evidence is split only across members that have observable signals. When no
    member-specific signal exists, contribution remains low rather than crediting
    every skill equally.
    """
    findings, evidence = _signals(output)
    names = [str(m.get("name", "")) for m in members if str(m.get("name", ""))]
    group_by_name = {
        str(name): index
        for index, group in enumerate(execution_groups, start=1)
        for name in group
    }
    rows: list[SkillExecutionEvidence] = []
    for member in members:
        name = str(member.get("name", ""))
        if not name:
            continue
        phase = str(member.get("phase", "general"))
        source = str(member.get("source", "unknown"))
        name_hits = len(re.findall(rf"(?i)\b{re.escape(name.replace('_', ' '))}\b", str(output)))
        role_bonus = 0.12 if role in {"verifier", "correctness reviewer", "security reviewer", "architecture reviewer"} and findings else 0.0
        signal_share = min(1.0, 0.25 * name_hits + 0.10 * evidence + 0.12 * findings + role_bonus)
        contribution = _clamp(0.45 * signal_share + 0.35 * _clamp(evidence_quality) + 0.20 * (1.0 if status == "passed" else 0.0))
        rows.append(SkillExecutionEvidence(
            skill=name, source=source, phase=phase, group=group_by_name.get(name, 0),
            status=status, evidence_quality=_clamp(evidence_quality),
            unique_findings=findings if name_hits else 0,
            evidence_signals=evidence,
            contribution=contribution,
            redundant=False,
        ))
    if len(rows) > 1:
        max_contribution = max(row.contribution for row in rows)
        rows = [
            SkillExecutionEvidence(**{**row.as_dict(), "redundant": row.contribution < max_contribution * 0.55})
            for row in rows
        ]
    return tuple(rows)


def assess_collaboration(
    *,
    bundle_id: str,
    bundle_quality: float,
    singleton_history: Mapping[str, Mapping[str, Any]],
    members: Iterable[str],
    bundle_cost: float,
    bundle_latency_seconds: float,
    min_delta: float = 0.05,
    max_incremental_cost: float = 0.35,
) -> CollaborationAssessment:
    baselines = [
        _clamp(float(singleton_history[name].get("evidence_quality", 0.0)))
        for name in members
        if name in singleton_history and singleton_history[name].get("samples", 0)
    ]
    baseline = max(baselines) if baselines else None
    delta = _clamp(bundle_quality) - baseline if baseline is not None else 0.0
    singleton_costs = [
        max(0.0, float(singleton_history[name].get("avg_cost", 0.0)))
        for name in members
        if name in singleton_history and singleton_history[name].get("samples", 0)
    ]
    incremental_cost = max(0.0, float(bundle_cost) - min(singleton_costs, default=float(bundle_cost)))
    promotable = bool(
        baseline is not None
        and delta >= float(min_delta)
        and incremental_cost <= float(max_incremental_cost)
        and len(tuple(members)) > 1
    )
    if baseline is None:
        reason = "no singleton baseline; retain as experimental"
    elif delta < min_delta:
        reason = "bundle did not add enough evidence over best singleton"
    elif incremental_cost > max_incremental_cost:
        reason = "incremental cost is too high for observed evidence gain"
    else:
        reason = "positive evidence delta with bounded incremental cost"
    return CollaborationAssessment(
        bundle_id=str(bundle_id), bundle_quality=_clamp(bundle_quality),
        singleton_baseline=baseline, collaboration_delta=delta,
        incremental_cost=incremental_cost,
        incremental_latency_seconds=max(0.0, float(bundle_latency_seconds)),
        promotable=promotable, reason=reason,
    )


def fingerprint(evidence: Sequence[SkillExecutionEvidence]) -> str:
    payload = [item.as_dict() for item in sorted(evidence, key=lambda item: (item.skill, item.group))]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


__all__ = ["SkillExecutionEvidence", "CollaborationAssessment", "attribute", "assess_collaboration", "fingerprint"]
