"""Execution-backed general intelligence evaluation protocol.

This module evaluates observed runtime behavior through executable runners and
independent oracles. It is an evaluation surface only: passing a gate never
grants execution authority and is not a claim of AGI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from .agi_evaluation import KINDS
from .benchmark_manifest import BenchmarkManifest

MINIMUM_DOMAINS = 4
MINIMUM_HOLDOUT_DOMAINS = 2
MINIMUM_KINDS = 6


@dataclass(frozen=True)
class ExecutionCase:
    case_id: str
    kind: str
    domain: str
    holdout: bool
    runner: Callable[[], Any]
    oracle: Callable[[Any], bool]
    evidence_factory: Callable[[Any], Iterable[str]]
    manifest: BenchmarkManifest | None = None
    oracle_id: str = ""
    manifest_digest: str = ""
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.case_id.strip() or not self.domain.strip():
            raise ValueError("case_id and domain are required")
        if self.kind not in KINDS:
            raise ValueError("unknown capability evaluation kind")
        if not callable(self.runner) or not callable(self.oracle):
            raise ValueError("runner and oracle must be callable")
        if not callable(self.evidence_factory):
            raise ValueError("evidence_factory must be callable")


@dataclass(frozen=True)
class ExecutionResult:
    case_id: str
    kind: str
    domain: str
    holdout: bool
    passed: bool
    verified: bool
    evidence_ids: tuple[str, ...]
    error: str | None = None


@dataclass(frozen=True)
class GeneralIntelligenceEvaluation:
    total: int
    passed: int
    verified: int
    domains: tuple[str, ...]
    holdout_domains: tuple[str, ...]
    kinds: tuple[str, ...]
    results: tuple[ExecutionResult, ...]
    breadth_passed: bool
    gate_passed: bool
    rationale: str

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def verification_rate(self) -> float:
        return self.verified / self.total if self.total else 0.0


class ExecutionBackedEvaluator:
    """Run bounded cases and require independent evidence before acceptance."""

    def __init__(
        self,
        *,
        minimum_domains: int = MINIMUM_DOMAINS,
        minimum_holdout_domains: int = MINIMUM_HOLDOUT_DOMAINS,
        minimum_kinds: int = MINIMUM_KINDS,
        minimum_pass_rate: float = 0.80,
        minimum_verification_rate: float = 1.0,
    ) -> None:
        self.minimum_domains = max(2, int(minimum_domains))
        self.minimum_holdout_domains = max(1, int(minimum_holdout_domains))
        self.minimum_kinds = max(1, int(minimum_kinds))
        self.minimum_pass_rate = max(0.0, min(1.0, float(minimum_pass_rate)))
        self.minimum_verification_rate = max(0.0, min(1.0, float(minimum_verification_rate)))

    def evaluate(self, cases: Iterable[ExecutionCase]) -> GeneralIntelligenceEvaluation:
        rows = tuple(cases)
        if not rows:
            raise ValueError("at least one execution case is required")

        ids = [case.case_id for case in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate execution case id")

        results: list[ExecutionResult] = []
        for case in rows:
            try:
                if case.manifest is not None:
                    manifest_ok, manifest_reason = case.manifest.validate(
                        domain=case.domain,
                        holdout=case.holdout,
                        oracle_id=case.oracle_id,
                        manifest_digest=case.manifest_digest,
                    )
                    if not manifest_ok:
                        raise ValueError(manifest_reason)
                observed = case.runner()
                passed = bool(case.oracle(observed))
                evidence = tuple(
                    str(item).strip()
                    for item in case.evidence_factory(observed)
                    if str(item).strip()
                )
                verified = passed and bool(evidence)
                error = None if verified else (
                    "missing independent evidence"
                    if passed and not evidence
                    else "oracle rejected observed behavior"
                )
            except Exception as exc:
                passed, verified, evidence = False, False, ()
                error = f"evaluation error: {exc}"
            results.append(
                ExecutionResult(
                    case.case_id,
                    case.kind,
                    case.domain,
                    case.holdout,
                    passed,
                    verified,
                    evidence,
                    error,
                )
            )

        domains = tuple(sorted({case.domain for case in rows}))
        holdout_domains = tuple(sorted({case.domain for case in rows if case.holdout}))
        kinds = tuple(sorted({case.kind for case in rows}))
        passed_count = sum(result.passed for result in results)
        verified_count = sum(result.verified for result in results)

        # Independence is evidence-level, not merely case-level: reusing the
        # same evidence identifier across cases cannot establish independent
        # validation.
        evidence_ids = [eid for result in results for eid in result.evidence_ids]
        independent = len(evidence_ids) == len(set(evidence_ids))
        breadth_passed = (
            independent
            and len(domains) >= self.minimum_domains
            and len(holdout_domains) >= self.minimum_holdout_domains
            and len(kinds) >= self.minimum_kinds
        )
        pass_rate = passed_count / len(results)
        verification_rate = verified_count / len(results)
        gate_passed = (
            breadth_passed
            and pass_rate >= self.minimum_pass_rate
            and verification_rate >= self.minimum_verification_rate
        )
        rationale = (
            "execution-backed breadth, holdouts, independent evidence, and outcome gates passed"
            if gate_passed
            else "evaluation gate not met; continue testing on independent evidence"
        )
        return GeneralIntelligenceEvaluation(
            len(results),
            passed_count,
            verified_count,
            domains,
            holdout_domains,
            kinds,
            tuple(results),
            breadth_passed,
            gate_passed,
            rationale,
        )


__all__ = [
    "ExecutionCase",
    "ExecutionResult",
    "GeneralIntelligenceEvaluation",
    "ExecutionBackedEvaluator",
]
