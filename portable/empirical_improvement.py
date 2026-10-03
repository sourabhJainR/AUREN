"""Replayable empirical improvement gates for integrated AUREN behavior."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ImprovementObservation:
    case_id: str
    strategy: str
    score: float
    iterations: int
    confidence: float
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.case_id.strip() or not self.strategy.strip():
            raise ValueError("case_id and strategy are required")
        if not 0 <= self.score <= 1:
            raise ValueError("score must be between 0 and 1")
        if self.iterations < 1:
            raise ValueError("iterations must be positive")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if any(not isinstance(item, str) or not item.strip() for item in self.evidence):
            raise ValueError("evidence must contain non-empty identifiers")


@dataclass(frozen=True)
class ImprovementReport:
    baseline_score: float
    candidate_score: float
    score_delta: float
    baseline_iterations: float
    candidate_iterations: float
    iteration_delta: float
    baseline_calibration_mae: float
    candidate_calibration_mae: float
    calibration_delta: float
    accepted: bool
    reason: str
    digest: str


class EmpiricalImprovement:
    """Compare verified strategy observations without changing execution authority."""

    @staticmethod
    def _metrics(observations: list[ImprovementObservation]) -> tuple[float, float, float]:
        if not observations:
            return 0.0, 0.0, 0.0
        score = sum(item.score for item in observations) / len(observations)
        iterations = sum(item.iterations for item in observations) / len(observations)
        calibration = sum(abs(item.confidence - item.score) for item in observations) / len(observations)
        return score, iterations, calibration

    @classmethod
    def evaluate(
        cls,
        baseline: Iterable[ImprovementObservation],
        candidate: Iterable[ImprovementObservation],
        *,
        min_score_gain: float = 0.01,
        max_iteration_increase: float = 0.0,
        max_calibration_degradation: float = 0.05,
    ) -> ImprovementReport:
        if min_score_gain < 0 or max_iteration_increase < 0 or max_calibration_degradation < 0:
            raise ValueError("improvement thresholds must be non-negative")
        base = sorted(list(baseline), key=lambda item: item.case_id)
        cand = sorted(list(candidate), key=lambda item: item.case_id)
        base_ids = [item.case_id for item in base]
        cand_ids = [item.case_id for item in cand]
        if len(set(base_ids)) != len(base_ids) or len(set(cand_ids)) != len(cand_ids):
            raise ValueError("duplicate benchmark case id")
        if base_ids != cand_ids:
            raise ValueError("baseline and candidate must cover identical case ids")
        if not base:
            return ImprovementReport(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, False, "empty benchmark", cls._digest(base, cand))
        if any(not item.evidence for item in cand):
            bscore, biter, bcal = cls._metrics(base)
            cscore, citer, ccal = cls._metrics(cand)
            return ImprovementReport(bscore, cscore, cscore - bscore, biter, citer, citer - biter,
                                     bcal, ccal, ccal - bcal, False, "candidate evidence is required", cls._digest(base, cand))
        bscore, biter, bcal = cls._metrics(base)
        cscore, citer, ccal = cls._metrics(cand)
        score_delta = cscore - bscore
        iteration_delta = citer - biter
        calibration_delta = ccal - bcal
        if score_delta < min_score_gain:
            accepted, reason = False, "quality improvement threshold not met"
        elif iteration_delta > max_iteration_increase:
            accepted, reason = False, "candidate iterations exceed the allowed increase"
        elif calibration_delta > max_calibration_degradation:
            accepted, reason = False, "confidence calibration degraded beyond the allowed bound"
        else:
            accepted, reason = True, "candidate shows evidence-backed empirical improvement"
        return ImprovementReport(
            round(bscore, 6), round(cscore, 6), round(score_delta, 6), round(biter, 6), round(citer, 6),
            round(iteration_delta, 6), round(bcal, 6), round(ccal, 6), round(calibration_delta, 6),
            accepted, reason, cls._digest(base, cand),
        )

    @staticmethod
    def _digest(baseline: list[ImprovementObservation], candidate: list[ImprovementObservation]) -> str:
        payload = {
            "baseline": [EmpiricalImprovement._serialize(item) for item in baseline],
            "candidate": [EmpiricalImprovement._serialize(item) for item in candidate],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @staticmethod
    def _serialize(item: ImprovementObservation) -> dict[str, object]:
        return {
            "case_id": item.case_id,
            "strategy": item.strategy,
            "score": item.score,
            "iterations": item.iterations,
            "confidence": item.confidence,
            "evidence": tuple(sorted(item.evidence)),
        }

    @classmethod
    def run(
        cls,
        cases: Iterable[object],
        baseline_strategy: str,
        candidate_strategy: str,
        evaluator,
        **thresholds,
    ) -> ImprovementReport:
        cases = list(cases)
        baseline = [evaluator(case, baseline_strategy) for case in cases]
        candidate = [evaluator(case, candidate_strategy) for case in cases]
        return cls.evaluate(baseline, candidate, **thresholds)


__all__ = ["EmpiricalImprovement", "ImprovementObservation", "ImprovementReport"]
