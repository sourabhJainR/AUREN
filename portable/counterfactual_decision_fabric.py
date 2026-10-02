"""Counterfactual evaluation for composed execution decisions.

This module deliberately stays advisory. It compares bounded strategy/mode
combinations using independently learned evidence and never overrides explicit
caller policy or safety constraints.
"""
from __future__ import annotations

from dataclasses import dataclass

from .execution_mode_learning import ExecutionMode, execution_mode
from .execution_strategy import ExecutionStrategy, execution_strategy
from .experience_router import ExperienceRouter
from .decision_context import DecisionContext, context_adjustment


@dataclass(frozen=True)
class DecisionCandidate:
    strategy: ExecutionStrategy
    mode: ExecutionMode
    score: float
    evidence_quality: float
    confidence: float
    samples: int
    rationale: str

    def as_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy.name,
            "mode": self.mode.name,
            "score": round(self.score, 3),
            "evidence_quality": round(self.evidence_quality, 3),
            "confidence": round(self.confidence, 3),
            "samples": self.samples,
            "rationale": self.rationale,
        }


class CounterfactualDecisionFabric:
    """Rank a small, deterministic set of independently evidenced policies."""

    STRATEGIES = ("default", "evidence-first", "deep-verify", "fast-path")
    MODES = ("balanced", "parallel", "serial", "verify-heavy")

    def __init__(self, root, *, minimum_samples: int = 3,
                 min_confidence: float = 0.65, min_margin: float = 0.05) -> None:
        self.root = root
        self.minimum_samples = max(1, int(minimum_samples))
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.min_margin = max(0.0, float(min_margin))

    @staticmethod
    def _key(role: str, task: str, kind: str, name: str) -> str:
        return f"{role}:{task[:96]}:{kind}:{name}"

    @staticmethod
    def _score(summary) -> float:
        return (
            0.40 * summary.evidence_quality
            + 0.30 * summary.success_rate
            + 0.20 * (1.0 - max(0.0, min(1.0, summary.avg_cost)))
            + 0.10 * (1.0 - max(0.0, min(1.0, summary.failure_rate)))
        )

    def evaluate(self, *, role: str, task: str,
                 baseline_strategy: str = "default",
                 baseline_mode: str = "balanced", context: DecisionContext | None = None) -> dict[str, object]:
        router = ExperienceRouter(self.root, minimum_samples=self.minimum_samples)
        strategy_rows = {
            name: router.summarize(self._key(role, task, "execution-strategy", name))
            for name in self.STRATEGIES
        }
        mode_rows = {
            name: router.summarize(self._key(role, task, "execution-mode", name))
            for name in self.MODES
        }
        candidates = []
        for strategy_name in self.STRATEGIES:
            strategy_summary = strategy_rows[strategy_name]
            if strategy_summary is None or strategy_summary.samples < self.minimum_samples or strategy_summary.confidence < self.min_confidence:
                continue
            for mode_name in self.MODES:
                mode_summary = mode_rows[mode_name]
                if mode_summary is None or mode_summary.samples < self.minimum_samples or mode_summary.confidence < self.min_confidence:
                    continue
                evidence = (strategy_summary.evidence_quality + mode_summary.evidence_quality) / 2.0
                confidence = min(strategy_summary.confidence, mode_summary.confidence)
                historical_score = (self._score(strategy_summary) + self._score(mode_summary)) / 2.0
                score = historical_score + (context_adjustment(strategy_name, mode_name, context) if context else 0.0)
                # Prefer compatible policies without pretending to have pairwise evidence.
                if strategy_name == "fast-path" and mode_name in {"serial", "verify-heavy"}:
                    score -= 0.03
                if strategy_name == "deep-verify" and mode_name == "parallel":
                    score -= 0.01
                candidates.append(DecisionCandidate(
                    execution_strategy(strategy_name), execution_mode(mode_name),
                    score, evidence, confidence,
                    min(strategy_summary.samples, mode_summary.samples),
                    ("counterfactual composition of independently observed strategy and mode evidence"
                     + (" with bounded workload-context adaptation" if context else "")),
                ))
        baseline = next(
            (x for x in candidates if x.strategy.name == baseline_strategy and x.mode.name == baseline_mode),
            None,
        )
        if not candidates:
            baseline = DecisionCandidate(
                execution_strategy(baseline_strategy), execution_mode(baseline_mode),
                0.0, 0.0, 0.0, 0, "no composed policy has sufficient evidence",
            )
            return {"selected": baseline.as_dict(), "changed": False, "candidates": 0,
                    "context": context.as_dict() if context else None}
        candidates.sort(key=lambda x: (-x.score, -x.confidence, x.strategy.name, x.mode.name))
        best = candidates[0]
        if baseline is None:
            baseline = DecisionCandidate(
                execution_strategy(baseline_strategy), execution_mode(baseline_mode),
                0.0, 0.0, 0.0, 0, "baseline has insufficient historical evidence",
            )
        changed = (best.strategy.name, best.mode.name) != (baseline.strategy.name, baseline.mode.name)
        if changed and best.score < baseline.score + self.min_margin:
            best = baseline
            changed = False
        return {
            "selected": best.as_dict(),
            "baseline": baseline.as_dict(),
            "changed": changed,
            "candidates": len(candidates),
            "margin": round(best.score - baseline.score, 3),
            "context": context.as_dict() if context else None,
        }


__all__ = ["CounterfactualDecisionFabric", "DecisionCandidate"]
