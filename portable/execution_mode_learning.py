"""Evidence-driven execution-mode learning for the decision fabric.

The mode is an advisory, bounded policy over parallelism, verification,
retry/escalation and resource use. Explicit caller policy and deterministic
safety limits remain authoritative.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .experience_router import ExperienceRouter
from .learning_steward import LearningSteward


@dataclass(frozen=True)
class ExecutionMode:
    name: str
    max_parallelism: int
    verification_depth: str
    retry_policy: str
    resource_fraction: float

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "max_parallelism": self.max_parallelism,
            "verification_depth": self.verification_depth,
            "retry_policy": self.retry_policy,
            "resource_fraction": round(self.resource_fraction, 3),
        }


@dataclass(frozen=True)
class ModeSelection:
    mode: ExecutionMode
    learned: bool
    confidence: float
    samples: int
    rationale: str

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.as_dict(),
            "learned": self.learned,
            "confidence": round(self.confidence, 3),
            "samples": self.samples,
            "rationale": self.rationale,
        }


_MODES = {
    "balanced": ExecutionMode("balanced", 4, "standard", "stop", 1.0),
    "parallel": ExecutionMode("parallel", 4, "standard", "retry", 1.0),
    "serial": ExecutionMode("serial", 1, "deep", "retry", 0.70),
    "verify-heavy": ExecutionMode("verify-heavy", 2, "independent", "escalate", 0.85),
}


def execution_mode(name: str | None) -> ExecutionMode:
    key = str(name or "balanced").strip().lower()
    return _MODES.get(key, _MODES["balanced"])


class ExecutionModeLearner:
    """Select a bounded execution mode from exact historical outcomes."""

    CANDIDATES = tuple(_MODES)

    def __init__(self, root: Path, *, minimum_samples: int = 3,
                 min_confidence: float = 0.65, min_margin: float = 0.04) -> None:
        self.root = Path(root)
        self.minimum_samples = max(1, int(minimum_samples))
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.min_margin = max(0.0, float(min_margin))

    @staticmethod
    def _key(role: str, task: str, mode: str) -> str:
        return f"{role}:{task[:96]}:execution-mode:{mode}"

    @staticmethod
    def _score(summary) -> float:
        return (
            0.45 * summary.evidence_quality
            + 0.30 * summary.success_rate
            + 0.15 * (1.0 - max(0.0, min(1.0, summary.avg_cost)))
            + 0.10 * (1.0 - max(0.0, min(1.0, summary.failure_rate)))
        )

    def select(self, *, role: str, task: str,
               baseline: str = "balanced") -> ModeSelection:
        baseline_mode = execution_mode(baseline)
        router = ExperienceRouter(self.root, minimum_samples=self.minimum_samples)
        summaries = {
            name: router.summarize(self._key(role, task, name))
            for name in self.CANDIDATES
        }
        eligible = [
            (self._score(summary), summary.confidence, name, summary.samples)
            for name, summary in summaries.items()
            if summary is not None
            and summary.samples >= self.minimum_samples
            and summary.confidence >= self.min_confidence
        ]
        if not eligible:
            return ModeSelection(
                baseline_mode, False, 0.0, 0,
                "no execution-mode history meets the confidence gate; retain baseline",
            )
        eligible.sort(key=lambda item: (-item[0], -item[1], item[2]))
        best_score, confidence, name, samples = eligible[0]
        baseline_summary = summaries.get(baseline_mode.name)
        baseline_score = (
            self._score(baseline_summary)
            if baseline_summary is not None
            and baseline_summary.samples >= self.minimum_samples
            and baseline_summary.confidence >= self.min_confidence
            else None
        )
        if name != baseline_mode.name and baseline_score is not None and best_score < baseline_score + self.min_margin:
            return ModeSelection(
                baseline_mode, False, baseline_summary.confidence, baseline_summary.samples,
                f"learned mode {name} did not beat baseline by {self.min_margin:.2f}",
            )
        return ModeSelection(
            execution_mode(name), name != baseline_mode.name, confidence, samples,
            f"repeated execution-mode evidence selected {name} with bounded score {best_score:.3f}",
        )

    def record(self, *, role: str, task: str, mode: str, outcome: str,
               evidence_quality: float, cost_score: float,
               duration_seconds: float, verification: str, retry: str,
               resource_fraction: float, parallelism: int,
               evidence_ids=()) -> None:
        LearningSteward(
            self.root, run_id="execution-mode-learning", task=task
        ).record_experience(
            key=self._key(role, task, mode),
            outcome=outcome,
            evidence_quality=evidence_quality,
            cost_score=cost_score,
            duration_seconds=duration_seconds,
            decision=json.dumps({
                "mode": mode,
                "verification": verification,
                "retry": retry,
                "resource_fraction": round(float(resource_fraction), 3),
                "parallelism": int(parallelism),
            }, sort_keys=True),
            evidence_ids=list(evidence_ids),
        )


__all__ = ["ExecutionMode", "ExecutionModeLearner", "ModeSelection", "execution_mode"]
