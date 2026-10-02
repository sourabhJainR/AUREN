"""Evidence-driven learning for bounded execution strategy selection.

Strategy history is advisory: explicit caller choices remain authoritative,
and learned strategies require repeated evidence and confidence before reuse.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .experience_router import ExperienceRouter
from .execution_strategy import ExecutionStrategy, execution_strategy


@dataclass(frozen=True)
class StrategySelection:
    strategy: ExecutionStrategy
    learned: bool
    confidence: float
    samples: int
    rationale: str

    def as_dict(self) -> dict[str, object]:
        return {"strategy": self.strategy.name, "learned": self.learned,
                "confidence": round(self.confidence, 3), "samples": self.samples,
                "rationale": self.rationale}


class ExecutionStrategyLearner:
    """Select built-in execution strategies from repeated observed outcomes."""

    CANDIDATES = ("default", "evidence-first", "deep-verify", "fast-path")

    def __init__(self, root: Path, *, minimum_samples: int = 2,
                 min_confidence: float = 0.55, min_margin: float = 0.04) -> None:
        self.root = Path(root)
        self.minimum_samples = max(1, int(minimum_samples))
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.min_margin = max(0.0, float(min_margin))

    @staticmethod
    def _key(role: str, task: str, strategy: str) -> str:
        return f"{role}:{task[:96]}:execution-strategy:{strategy}"

    @staticmethod
    def _score(summary) -> float:
        return (0.50 * summary.evidence_quality
                + 0.30 * summary.success_rate
                + 0.15 * (1.0 - max(0.0, min(1.0, summary.avg_cost)))
                + 0.05 * (1.0 - max(0.0, min(1.0, summary.failure_rate))))

    def select(self, *, role: str, task: str, baseline: str = "default") -> StrategySelection:
        baseline_profile = execution_strategy(baseline)
        router = ExperienceRouter(self.root, minimum_samples=self.minimum_samples)
        summaries = {name: router.summarize(self._key(role, task, name))
                     for name in self.CANDIDATES}
        eligible = [
            (self._score(summary), summary.confidence, name, summary.samples)
            for name, summary in summaries.items()
            if summary is not None
            and summary.samples >= self.minimum_samples
            and summary.confidence >= self.min_confidence
        ]
        if not eligible:
            return StrategySelection(
                baseline_profile, False, 0.0, 0,
                "no strategy has enough confident historical evidence; retain baseline",
            )
        eligible.sort(key=lambda item: (-item[0], -item[1], item[2]))
        best_score, confidence, name, samples = eligible[0]
        baseline_summary = summaries.get(baseline_profile.name)
        baseline_score = (
            self._score(baseline_summary)
            if baseline_summary is not None
            and baseline_summary.samples >= self.minimum_samples
            and baseline_summary.confidence >= self.min_confidence
            else None
        )
        if name != baseline_profile.name and baseline_score is not None and best_score < baseline_score + self.min_margin:
            return StrategySelection(
                baseline_profile, False, baseline_summary.confidence, baseline_summary.samples,
                f"learned candidate {name} did not beat baseline by {self.min_margin:.2f}",
            )
        return StrategySelection(
            execution_strategy(name), name != baseline_profile.name, confidence, samples,
            f"repeated strategy evidence selected {name} with bounded score {best_score:.3f}",
        )


    @staticmethod
    def observed_evidence_quality(result: object) -> float:
        """Derive strategy evidence from actual executed-group telemetry."""
        pathway = getattr(result, "pathway", {})
        rows = pathway.get("skill_group_evidence", ()) if isinstance(pathway, dict) else ()
        values = []
        for row in rows:
            if isinstance(row, dict):
                try:
                    values.append(float(row.get("useful_evidence", row.get("evidence_quality", 0.0))))
                except (TypeError, ValueError):
                    continue
        if values:
            return max(0.0, min(1.0, sum(values) / len(values)))
        return max(0.0, min(1.0, float(getattr(result, "capability_bundle_score", 0.0) or 0.0)))

    def record(self, *, role: str, task: str, strategy: str, outcome: str,
               evidence_quality: float, cost_score: float, duration_seconds: float,
               verification: str, retry: str, evidence_ids: Sequence[str]) -> None:
        from .learning_steward import LearningSteward
        LearningSteward(self.root, run_id="execution-strategy-learning", task=task).record_experience(
            key=self._key(role, task, strategy), outcome=outcome,
            evidence_quality=max(0.0, min(1.0, float(evidence_quality))),
            cost_score=max(0.0, min(1.0, float(cost_score))),
            duration_seconds=max(0.0, float(duration_seconds)),
            decision=json.dumps({"strategy": strategy, "verification": verification, "retry": retry}, sort_keys=True),
            evidence_ids=list(evidence_ids),
        )


__all__ = ["ExecutionStrategyLearner", "StrategySelection"]
