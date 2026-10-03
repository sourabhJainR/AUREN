"""Discover new task families and environment dimensions from external outcomes.

This module proposes exploration targets only. It never executes a task, creates
an oracle, or mutates the capability registry. Discovery is deliberately driven
by observed transfer failures so the next evaluation can be genuinely different
from the failed case.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable


@dataclass(frozen=True, slots=True)
class DiscoverySignal:
    source_evidence_id: str
    failed_domain: str
    failed_constraint: str
    observed_gap: float
    novel_tool_required: bool = False

    def __post_init__(self) -> None:
        if not self.source_evidence_id.strip() or not self.failed_domain.strip():
            raise ValueError("evidence and domain are required")
        if not self.failed_constraint.strip():
            raise ValueError("failed_constraint is required")
        if not 0.0 <= self.observed_gap <= 1.0:
            raise ValueError("observed_gap must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ExplorationTarget:
    target_id: str
    task_family: str
    environment_dimension: str
    constraints: tuple[str, ...]
    required_modalities: tuple[str, ...]
    unfamiliar_tools: tuple[str, ...]
    source_evidence_ids: tuple[str, ...]
    novelty_score: float
    holdout_required: bool
    independent_oracle_required: bool
    target_digest: str

    @property
    def trustworthy(self) -> bool:
        return (
            self.holdout_required
            and self.independent_oracle_required
            and bool(self.source_evidence_ids)
            and self.novelty_score > 0
        )


class OpenEndedTaskEnvironmentDiscovery:
    """Turn external failures into bounded, novel evaluation targets."""

    def discover(
        self,
        signals: Iterable[DiscoverySignal],
        *,
        max_targets: int = 8,
    ) -> tuple[ExplorationTarget, ...]:
        if max_targets < 1:
            raise ValueError("max_targets must be positive")
        unique: dict[tuple[str, str], list[DiscoverySignal]] = {}
        for signal in signals:
            key = (signal.failed_domain, signal.failed_constraint)
            unique.setdefault(key, []).append(signal)

        ranked = sorted(
            unique.values(),
            key=lambda group: (
                -max(s.observed_gap for s in group),
                group[0].failed_domain,
                group[0].failed_constraint,
            ),
        )
        targets = []
        for group in ranked[:max_targets]:
            domain = group[0].failed_domain
            constraint = group[0].failed_constraint
            evidence_ids = tuple(sorted({s.source_evidence_id for s in group}))
            tools = tuple(sorted({
                f"unfamiliar:{domain}:{constraint}"
                for s in group if s.novel_tool_required
            }))
            task_family = f"novel-{domain}-{constraint}"
            dimension = self._dimension(group)
            constraints = (constraint, "unseen-task-instance", "fresh-environment")
            modalities = ("text", "structured") if not tools else ("text", "structured", "tool")
            novelty = min(1.0, max(s.observed_gap for s in group) + 0.2)
            payload = {
                "task_family": task_family,
                "environment_dimension": dimension,
                "constraints": constraints,
                "required_modalities": modalities,
                "unfamiliar_tools": tools,
                "source_evidence_ids": evidence_ids,
            }
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            targets.append(
                ExplorationTarget(
                    "target-" + digest[:16],
                    task_family,
                    dimension,
                    constraints,
                    modalities,
                    tools,
                    evidence_ids,
                    novelty,
                    True,
                    True,
                    digest,
                )
            )
        return tuple(targets)

    @staticmethod
    def _dimension(signals: list[DiscoverySignal]) -> str:
        constraints = {s.failed_constraint for s in signals}
        if any("long-horizon" in c for c in constraints):
            return "long-horizon"
        if any(s.novel_tool_required for s in signals):
            return "unfamiliar-tool"
        if any("adversarial" in c for c in constraints):
            return "adversarial"
        return "constraint-shift"


__all__=["DiscoverySignal","ExplorationTarget","OpenEndedTaskEnvironmentDiscovery"]
