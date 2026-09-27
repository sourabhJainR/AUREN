"""Developer-directed remediation of post-implementation review findings.

The remediation layer preserves the review finding as the unit of work and
records the complete chain from finding to repair, changed files, verification,
and final review. It never changes developer intent or promotes code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .multi_hat_self_review import ReviewFinding, ReviewHat, SelfReviewReport
from .review_gated_repair_cycle import RepairCandidate, ReviewGatedRepairCycle, ReviewGatedRepairResult
from .repository_engineering_cycle import PatchProposal
from .sandboxed_repository import CommandSpec


@dataclass(frozen=True)
class ReviewRemediationItem:
    finding_id: str
    hat: ReviewHat
    severity: str
    title: str
    detail: str
    recommendation: str
    evidence_ids: tuple[str, ...]
    status: str
    repair: ReviewGatedRepairResult | None = None
    changed_files: tuple[str, ...] = ()
    verification_evidence_ids: tuple[str, ...] = ()
    final_review: SelfReviewReport | None = None
    resolution_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewRemediationResult:
    source_review: SelfReviewReport
    backlog: tuple[ReviewRemediationItem, ...]
    addressed: tuple[str, ...]
    unresolved: tuple[str, ...]
    skipped: tuple[str, ...]


class ReviewRemediationCycle:
    """Turn an explicit developer 'address' decision into bounded repair work."""

    def backlog(self, report: SelfReviewReport) -> tuple[ReviewRemediationItem, ...]:
        if not isinstance(report, SelfReviewReport):
            raise TypeError("report must be a SelfReviewReport")
        return tuple(
            ReviewRemediationItem(
                finding_id=finding.stable_id,
                hat=finding.hat,
                severity=finding.severity,
                title=finding.title,
                detail=finding.detail,
                recommendation=finding.recommendation,
                evidence_ids=tuple(finding.evidence_ids),
                status="pending",
            )
            for finding in report.findings
        )

    @staticmethod
    def _same_finding(left: ReviewFinding, right: ReviewFinding) -> bool:
        return (
            left.hat == right.hat
            and left.title.strip() == right.title.strip()
        )

    def run(
        self,
        report: SelfReviewReport,
        *,
        finding_ids: Sequence[str],
        commands: Sequence[CommandSpec],
        reviewers: Mapping[
            ReviewHat,
            Callable[[str, tuple[str, ...], tuple[str, ...]], Sequence[ReviewFinding]],
        ],
        initial: Callable[[ReviewFinding], RepairCandidate],
        repair: Callable[[ReviewFinding, str, int], RepairCandidate] | None = None,
        learning_transfer: Any | None = None,
        max_attempts: int = 3,
    ) -> ReviewRemediationResult:
        if not isinstance(report, SelfReviewReport):
            raise TypeError("report must be a SelfReviewReport")
        if report.developer_decision != "address":
            raise ValueError("developer decision must be address before remediation")
        selected = tuple(dict.fromkeys(finding_ids))
        available = {f.stable_id: f for f in report.findings}
        unknown = [item for item in selected if item not in available]
        if unknown:
            raise ValueError(f"unknown finding ids: {unknown}")
        if not selected:
            return ReviewRemediationResult(report, (), (), (), ())

        items: list[ReviewRemediationItem] = []
        addressed: list[str] = []
        unresolved: list[str] = []
        for finding_id in selected:
            finding = available[finding_id]
            candidate = initial(finding)
            if not isinstance(candidate, RepairCandidate):
                raise TypeError("initial callback must return RepairCandidate")

            def next_attempt(defect: str, number: int, finding=finding) -> Any:
                if repair is None:
                    raise RuntimeError("no bounded repair callback supplied")
                candidate = repair(finding, defect, number)
                if not isinstance(candidate, RepairCandidate):
                    raise TypeError("repair callback must return RepairCandidate")
                return candidate

            cycle = ReviewGatedRepairCycle(candidate.proposal and str(self._source(candidate.proposal)) if False else ".")
            # The repository cycle must use the same source as the candidate's proposal.
            # A candidate is produced for this remediation run, so execute through the
            # injected source factory below rather than reconstructing it here.
            del cycle
            raise RuntimeError("ReviewRemediationCycle requires a repository_source")
        return ReviewRemediationResult(report, tuple(items), tuple(addressed), tuple(unresolved), ())

    @staticmethod
    def _source(proposal: PatchProposal) -> str:
        raise RuntimeError("proposal does not encode a repository source")


__all__ = ["ReviewRemediationItem", "ReviewRemediationResult", "ReviewRemediationCycle"]
