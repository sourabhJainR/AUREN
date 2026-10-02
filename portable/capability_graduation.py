"""Evidence-gated lifecycle for transferable capabilities.

Graduation is deliberately separate from discovery: a pattern must survive
independent holdout cohorts, canary evidence, and regression checks before it
becomes reusable. Retirement is triggered by repeated verified regressions.
No execution authority is granted by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Iterable, Mapping

from .cross_task_capability_abstraction import CapabilityPattern, TransferValidation


@dataclass(frozen=True)
class CapabilityRollout:
    pattern_id: str
    state: str
    cohorts: int
    passing_cohorts: int
    regressions: int
    confidence: float
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "pattern_id": self.pattern_id, "state": self.state,
            "cohorts": self.cohorts, "passing_cohorts": self.passing_cohorts,
            "regressions": self.regressions, "confidence": round(self.confidence, 3),
            "reason": self.reason,
        }


class CapabilityGraduationController:
    """Promote, rollback, or retire abstractions using independent evidence."""

    def __init__(self, *, minimum_cohorts: int = 2, minimum_confidence: float = 0.70,
                 retirement_regressions: int = 2) -> None:
        self.minimum_cohorts = max(1, int(minimum_cohorts))
        self.minimum_confidence = max(0.0, min(1.0, float(minimum_confidence)))
        self.retirement_regressions = max(1, int(retirement_regressions))

    @staticmethod
    def _cohort_key(validation: TransferValidation) -> str:
        evidence = "|".join(sorted(validation.evidence_ids))
        return hashlib.sha256(f"{validation.pattern_id}|{evidence}".encode()).hexdigest()[:12]

    def evaluate(self, pattern: CapabilityPattern,
                 validations: Iterable[TransferValidation]) -> CapabilityRollout:
        rows = tuple(validations)
        if not rows:
            return CapabilityRollout(pattern.pattern_id, "candidate", 0, 0, 0, 0.0,
                                     "no independent transfer cohorts supplied")
        unique: dict[str, TransferValidation] = {}
        for row in rows:
            if row.pattern_id != pattern.pattern_id:
                continue
            if row.evidence_ids:
                unique[self._cohort_key(row)] = row
        cohorts = tuple(unique.values())
        passing = tuple(row for row in cohorts if row.promoted and row.verified and row.regression_passed)
        regressions = sum(1 for row in cohorts if row.verified and (not row.regression_passed or row.uplift < 0))
        confidence = min(1.0, (len(passing) / max(1, self.minimum_cohorts)) *
                         (sum(max(0.0, min(1.0, row.candidate_score)) for row in passing) / max(1, len(passing))))
        if regressions >= self.retirement_regressions:
            return CapabilityRollout(pattern.pattern_id, "retired", len(cohorts), len(passing),
                                     regressions, confidence, "repeated verified transfer regressions")
        if len(passing) >= self.minimum_cohorts and confidence >= self.minimum_confidence:
            return CapabilityRollout(pattern.pattern_id, "promoted", len(cohorts), len(passing),
                                     regressions, confidence, "independent holdout cohorts passed graduation gates")
        if passing:
            return CapabilityRollout(pattern.pattern_id, "canary", len(cohorts), len(passing),
                                     regressions, confidence, "transfer evidence is positive but graduation gate is incomplete")
        return CapabilityRollout(pattern.pattern_id, "candidate", len(cohorts), 0, regressions,
                                 confidence, "insufficient independent evidence for promotion")


__all__ = ["CapabilityGraduationController", "CapabilityRollout"]
