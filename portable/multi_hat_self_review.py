"""Independent multi-hat post-implementation review."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping, Sequence


class ReviewHat(str, Enum):
    ARCHITECT = "architectural"
    IMPLEMENTATION = "senior-implementation"
    QUALITY = "senior-quality"
    SECURITY = "senior-security"
    PERFORMANCE = "senior-performance"
    END_USER = "end-user"
    PRODUCT = "pm-stakeholder"


@dataclass(frozen=True)
class ReviewFinding:
    hat: ReviewHat
    severity: str
    title: str
    detail: str
    recommendation: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SelfReviewReport:
    findings: tuple[ReviewFinding, ...]
    reviewed_hats: tuple[ReviewHat, ...]
    evidence_ids: tuple[str, ...]
    developer_decision: str = "pending"

    @property
    def has_blocking_findings(self) -> bool:
        return any(f.severity.lower() == "blocker" for f in self.findings)

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)

    def decide(self, decision: str) -> "SelfReviewReport":
        decision = decision.strip().lower()
        if decision not in {"stop", "address", "accept"}:
            raise ValueError("decision must be stop, address, or accept")
        return SelfReviewReport(
            self.findings, self.reviewed_hats, self.evidence_ids, decision
        )


class MultiHatSelfReview:
    """Runs independent role-oriented reviews after implementation.

    Findings are advisory. No hat can mutate code, promote a capability, or
    override the developer's explicit stop/address/accept decision.
    """

    HATS = tuple(ReviewHat)

    def review(
        self,
        *,
        implementation: str,
        changed_files: Sequence[str],
        evidence_ids: Sequence[str],
        reviewers: Mapping[ReviewHat, Callable[[str, tuple[str, ...], tuple[str, ...]], Sequence[ReviewFinding]]],
    ) -> SelfReviewReport:
        if not implementation.strip():
            raise ValueError("implementation summary is required")
        if not changed_files:
            raise ValueError("changed_files are required")
        evidence = tuple(dict.fromkeys(evidence_ids))
        findings: list[ReviewFinding] = []
        reviewed: list[ReviewHat] = []
        for hat in self.HATS:
            reviewer = reviewers.get(hat)
            if reviewer is None:
                raise ValueError(f"missing reviewer for {hat.value}")
            result = tuple(reviewer(implementation, tuple(changed_files), evidence))
            for finding in result:
                if finding.hat != hat:
                    raise ValueError("review finding attributed to wrong hat")
                findings.append(finding)
            reviewed.append(hat)
        return SelfReviewReport(tuple(findings), tuple(reviewed), evidence)


__all__ = ["ReviewHat", "ReviewFinding", "SelfReviewReport", "MultiHatSelfReview"]
