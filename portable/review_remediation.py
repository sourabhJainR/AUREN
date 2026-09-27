"""Developer-directed remediation of post-implementation review findings.

The remediation layer preserves each finding as the unit of work and records
finding -> repair attempt -> changed files -> verification -> final review.
It never changes developer intent or promotes code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .multi_hat_self_review import ReviewFinding, ReviewHat, SelfReviewReport
from .review_gated_repair_cycle import RepairCandidate, ReviewGatedRepairCycle, ReviewGatedRepairResult
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

    def __init__(self, source: str) -> None:
        self.source = source

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
        return left.hat == right.hat and left.title.strip() == right.title.strip()

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
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")

        selected = tuple(dict.fromkeys(finding_ids))
        available = {f.stable_id: f for f in report.findings}
        unknown = [item for item in selected if item not in available]
        if unknown:
            raise ValueError(f"unknown finding ids: {unknown}")

        selected_set = set(selected)
        skipped = tuple(f.stable_id for f in report.findings if f.stable_id not in selected_set)
        items: list[ReviewRemediationItem] = []
        addressed: list[str] = []
        unresolved: list[str] = []

        for finding_id in selected:
            finding = available[finding_id]
            candidate = initial(finding)
            if not isinstance(candidate, RepairCandidate):
                raise TypeError("initial callback must return RepairCandidate")

            def next_attempt(defect: str, number: int, finding=finding) -> RepairCandidate:
                if repair is None:
                    raise RuntimeError("no bounded repair callback supplied")
                candidate = repair(finding, defect, number)
                if not isinstance(candidate, RepairCandidate):
                    raise TypeError("repair callback must return RepairCandidate")
                return candidate

            cycle_result = ReviewGatedRepairCycle(self.source).run(
                candidate.proposal,
                commands=commands,
                reviewers=reviewers,
                repair=next_attempt if repair is not None else None,
                baseline_score=0.0,
                max_attempts=max_attempts,
            )
            final_review = cycle_result.review
            still_present = any(
                self._same_finding(finding, reviewed_finding)
                for reviewed_finding in final_review.findings
            )
            resolved = cycle_result.final.accepted and not still_present
            status = "resolved" if resolved else "unresolved"
            resolution_evidence = tuple(dict.fromkeys(
                cycle_result.final.evidence_ids
                + tuple(
                    attempt.evidence_id
                    for attempt in (cycle_result.repair.attempts if cycle_result.repair else ())
                    if attempt.evidence_id
                )
            ))

            if resolved:
                addressed.append(finding_id)
            else:
                unresolved.append(finding_id)
                if learning_transfer is not None and resolution_evidence:
                    learning_transfer.record_failure(
                        problem=finding.detail,
                        dont=(
                            f"Remediation did not resolve review finding '{finding.title}': "
                            f"{finding.recommendation}"
                        ),
                        evidence_ids=resolution_evidence,
                        confidence=0.95,
                    )

            items.append(ReviewRemediationItem(
                finding_id=finding_id,
                hat=finding.hat,
                severity=finding.severity,
                title=finding.title,
                detail=finding.detail,
                recommendation=finding.recommendation,
                evidence_ids=finding.evidence_ids,
                status=status,
                repair=cycle_result,
                changed_files=tuple(cycle_result.final.execution.changed_files),
                verification_evidence_ids=tuple(cycle_result.final.evidence_ids),
                final_review=final_review,
                resolution_evidence_ids=resolution_evidence,
            ))

        return ReviewRemediationResult(
            report, tuple(items), tuple(addressed), tuple(unresolved), skipped
        )


__all__ = ["ReviewRemediationItem", "ReviewRemediationResult", "ReviewRemediationCycle"]
