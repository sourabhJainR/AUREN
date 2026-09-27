"""Review-gated bounded repository repair."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .multi_hat_self_review import ReviewFinding, ReviewHat, SelfReviewReport
from .repository_engineering_cycle import PatchProposal, RepositoryEngineeringCycle, RepositoryEngineeringCycleResult
from .sandboxed_repository import CommandSpec
from .verified_repair_loop import RepairAttempt, RepairReport, VerifiedRepairLoop


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
        repair: Callable[[str, int], RepairAttempt] | None = None,
        baseline_score: float = 0.0,
        max_attempts: int = 3,
    ) -> ReviewGatedRepairResult:
        first = self.engineering.run(initial, commands=commands)
        repair_report = None
        final = first

        if not first.accepted and repair is not None:
            repair_report = VerifiedRepairLoop().run(
                first.rejection_reason or "repository verification failed",
                baseline_score,
                repair,
                max_attempts=max_attempts,
            )
            if repair_report.accepted:
                candidate = getattr(repair, "proposal", None)
                if isinstance(candidate, PatchProposal):
                    final = self.engineering.run(candidate, commands=commands)

        reviewed = self.engineering.review(final, reviewers=reviewers)
        return ReviewGatedRepairResult(first, repair_report, final, reviewed.self_review)


__all__ = ["ReviewGatedRepairCycle", "ReviewGatedRepairResult"]
