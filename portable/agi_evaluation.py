"""Dependency-free capability evaluation primitives for AUREN.

The suite measures general behaviors such as transfer, novelty, memory,
reasoning, causal thinking, self-correction and long-horizon execution. It is
an evaluation surface only: results cannot grant autonomy or change policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

KINDS = frozenset({
    "novel", "transfer", "memory", "reasoning", "causal",
    "self_correction", "long_horizon", "adversarial", "calibration", "continual_learning",
})


@dataclass(frozen=True)
class CapabilityCase:
    case_id: str
    kind: str
    expected: str
    observed: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (("case_id", self.case_id), ("expected", self.expected), ("observed", self.observed)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.kind not in KINDS:
            raise ValueError("unknown capability evaluation kind")
        if any(not isinstance(item, str) or not item.strip() for item in self.evidence):
            raise ValueError("evidence must contain non-empty identifiers")


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    kind: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class EvaluationReport:
    total: int
    passed: int
    failed: int
    coverage: tuple[str, ...]
    results: tuple[CaseResult, ...]

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def missing_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(KINDS.difference(self.coverage)))


class CapabilityEvaluator:
    """Run deterministic capability checks with explicit evidence requirements."""

    def evaluate(self, cases: Iterable[CapabilityCase]) -> EvaluationReport:
        materialized = list(cases)
        seen: set[str] = set()
        results: list[CaseResult] = []
        for case in materialized:
            if case.case_id in seen:
                raise ValueError("duplicate capability case id")
            seen.add(case.case_id)
            if not case.evidence:
                results.append(CaseResult(case.case_id, case.kind, False, "missing evidence"))
                continue
            passed = case.expected.strip() == case.observed.strip()
            results.append(CaseResult(case.case_id, case.kind, passed,
                                      "observed behavior matches expected behavior" if passed else "observed behavior differs from expected behavior"))
        passed = sum(1 for result in results if result.passed)
        coverage = tuple(sorted({case.kind for case in materialized}))
        return EvaluationReport(len(results), passed, len(results) - passed, coverage, tuple(results))


__all__ = ["CapabilityCase", "CapabilityEvaluator", "CaseResult", "EvaluationReport", "KINDS"]
