"""Context-specific outcome learning for execution decisions.

Learning is indexed by a stable workload-context fingerprint. Exact-context
evidence is preferred; global role/task evidence remains the bounded fallback
for cold-start and sparse contexts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .decision_context import DecisionContext
from .experience_router import ExperienceRouter
from .learning_steward import LearningSteward


@dataclass(frozen=True)
class ContextPolicySelection:
    strategy: str
    mode: str
    learned: bool
    confidence: float
    samples: int
    rationale: str

    def as_dict(self) -> dict[str, object]:
        return {"strategy": self.strategy, "mode": self.mode,
                "learned": self.learned, "confidence": round(self.confidence, 3),
                "samples": self.samples, "rationale": self.rationale}


class ContextSpecificDecisionLearner:
    """Learn policy outcomes conditional on the current workload context."""

    def __init__(self, root: Path, *, minimum_samples: int = 3,
                 min_confidence: float = 0.65, min_margin: float = 0.04) -> None:
        self.root = Path(root)
        self.minimum_samples = max(1, int(minimum_samples))
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.min_margin = max(0.0, float(min_margin))

    @staticmethod
    def _key(role: str, task: str, context: DecisionContext, strategy: str, mode: str) -> str:
        return f"{role}:{task[:96]}:decision-context:{context.fingerprint}:{strategy}:{mode}"

    @staticmethod
    def _score(summary) -> float:
        return (0.45 * summary.evidence_quality
                + 0.30 * summary.success_rate
                + 0.15 * (1.0 - max(0.0, min(1.0, summary.avg_cost)))
                + 0.10 * (1.0 - max(0.0, min(1.0, summary.failure_rate))))

    def select(self, *, role: str, task: str, context: DecisionContext,
               baseline_strategy: str = "default",
               baseline_mode: str = "balanced",
               candidates: tuple[tuple[str, str], ...] = ()) -> ContextPolicySelection:
        pairs = candidates or tuple(
            (strategy, mode)
            for strategy in ("default", "evidence-first", "deep-verify", "fast-path")
            for mode in ("balanced", "parallel", "serial", "verify-heavy")
        )
        router = ExperienceRouter(self.root, minimum_samples=self.minimum_samples)
        eligible = []
        for strategy, mode in pairs:
            summary = router.summarize(self._key(role, task, context, strategy, mode))
            if summary is None or summary.samples < self.minimum_samples or summary.confidence < self.min_confidence:
                continue
            eligible.append((self._score(summary), summary.confidence, strategy, mode, summary.samples))
        baseline_summary = router.summarize(self._key(role, task, context, baseline_strategy, baseline_mode))
        baseline_score = (
            self._score(baseline_summary)
            if baseline_summary is not None
            and baseline_summary.samples >= self.minimum_samples
            and baseline_summary.confidence >= self.min_confidence
            else None
        )
        if not eligible:
            return ContextPolicySelection(
                baseline_strategy, baseline_mode, False, 0.0, 0,
                "no context-specific history meets the confidence gate; retain baseline",
            )
        eligible.sort(key=lambda x: (-x[0], -x[1], x[2], x[3]))
        score, confidence, strategy, mode, samples = eligible[0]
        if (strategy, mode) == (baseline_strategy, baseline_mode):
            return ContextPolicySelection(strategy, mode, False, confidence, samples,
                                          "context-specific evidence supports the baseline")
        if baseline_score is not None and score < baseline_score + self.min_margin:
            return ContextPolicySelection(
                baseline_strategy, baseline_mode, False, baseline_summary.confidence,
                baseline_summary.samples,
                f"context-specific candidate {strategy}+{mode} did not beat baseline by {self.min_margin:.2f}",
            )
        return ContextPolicySelection(
            strategy, mode, True, confidence, samples,
            f"context-specific evidence selected {strategy}+{mode} with bounded score {score:.3f}",
        )

    def record(self, *, role: str, task: str, context: DecisionContext,
               strategy: str, mode: str, outcome: str,
               evidence_quality: float, cost_score: float,
               duration_seconds: float, evidence_ids=()) -> None:
        LearningSteward(self.root, run_id="decision-context-learning", task=task).record_experience(
            key=self._key(role, task, context, strategy, mode),
            outcome=outcome,
            evidence_quality=evidence_quality,
            cost_score=cost_score,
            duration_seconds=duration_seconds,
            decision=json.dumps({"context": context.as_dict(), "strategy": strategy, "mode": mode},
                                sort_keys=True),
            evidence_ids=list(evidence_ids),
        )


__all__ = ["ContextPolicySelection", "ContextSpecificDecisionLearner"]
