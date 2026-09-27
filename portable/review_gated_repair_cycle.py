"""Review-gated bounded repository repair."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .multi_hat_self_review import ReviewFinding, ReviewHat, SelfReviewReport
from .repository_engineering_cycle import PatchProposal, RepositoryEngineeringCycle, RepositoryEngineeringCycleResult
from .sandboxed_repository import CommandSpec
from .verified_repair_loop import RepairAttempt, RepairReport, VerifiedRepairLoop


@dataclass(frozen=True)
class RepairCandidate:
    attempt: RepairAttempt
    proposal: PatchProposal


@dataclass(frozen=True)
class ReviewGatedRepairResult:
    initial: RepositoryEngineeringCycleResult
    repair: RepairReport | None
    final: RepositoryEngineeringCycleResult
    review: SelfReviewReport


class ReviewGatedRepairCycle:
    def __init__(self, source: str) -> None:
        self.engineering = RepositoryEngineeringCycle(source)

    def run(
        self,
        initial: PatchProposal,
        *,
        commands: Sequence[CommandSpec],
        reviewers: Mapping[
            ReviewHat,
            Callable[[str, tuple[str, ...], tuple[str, ...]], Sequence[ReviewFinding]],
        ],
        repair: Callable[[str, int], RepairCandidate] | None = None,
        baseline_score: float = 0.0,
        max_attempts: int = 3,
    ) -> ReviewGatedRepairResult:
        first = self.engineering.run(initial, commands=commands)
        repair_report = None
        final = first

        if not first.accepted and repair is not None:
            candidate_results: list[RepairCandidate] = []
            def attempt(defect: str, number: int) -> RepairAttempt:
                candidate = repair(defect, number)
                if not isinstance(candidate, RepairCandidate):
                    raise TypeError("repair callback must return RepairCandidate")
                candidate_results.append(candidate)
                return candidate.attempt
            repair_report = VerifiedRepairLoop().run(first.rejection_reason or "repository verification failed", baseline_score, attempt, max_attempts=max_attempts)
            if repair_report.accepted:
                chosen = next((c for c in reversed(candidate_results) if c.attempt.verified and c.attempt.score == repair_report.final_score), None)
                if chosen is not None:
                    final = self.engineering.run(chosen.proposal, commands=commands)

        reviewed = self.engineering.review(final, reviewers=reviewers)
        return ReviewGatedRepairResult(first, repair_report, final, reviewed.self_review)


__all__ = ["RepairCandidate", "ReviewGatedRepairCycle", "ReviewGatedRepairResult"]
