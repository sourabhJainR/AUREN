"""Canonical closed-loop engineering episode.

An EngineeringEpisode is the single lifecycle record that ties repository facts,
planning, execution, evidence, verification, review, repair, outcome and
learning together. It is an immutable value object: every transition returns
a new episode and points to the previous digest.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
from typing import Any, Iterable

from .engineering_evidence_envelope import EngineeringEvidenceEnvelope


class EpisodePhase(str, Enum):
    INTAKE = "intake"
    PLANNED = "planned"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    REPAIRING = "repairing"
    COMPLETED = "completed"
    FAILED = "failed"


class EpisodeGateError(ValueError):
    """Raised when an episode transition violates a fail-closed gate."""


@dataclass(frozen=True, slots=True)
class EpisodeFinding:
    finding_id: str
    hat: str
    severity: str
    detail: str
    evidence_ids: tuple[str, ...] = ()
    recommendation: str = ""

    def __post_init__(self) -> None:
        if not self.finding_id.strip() or not self.hat.strip() or not self.detail.strip():
            raise ValueError("finding_id, hat and detail are required")
        if self.severity.lower() not in {"blocker", "critical", "high", "medium", "low"}:
            raise ValueError("unsupported finding severity")
        if any(not x.strip() for x in self.evidence_ids):
            raise ValueError("finding evidence ids cannot be empty")


@dataclass(frozen=True, slots=True)
class EngineeringEpisode:
    episode_id: str
    task_id: str
    project: str
    repository_snapshot: str
    intent_digest: str
    phase: EpisodePhase = EpisodePhase.INTAKE
    plan_digest: str = ""
    capability: str = ""
    resource_lane: str = ""
    evidence_ids: tuple[str, ...] = ()
    verification_ids: tuple[str, ...] = ()
    review_ids: tuple[str, ...] = ()
    regression_ids: tuple[str, ...] = ()
    findings: tuple[EpisodeFinding, ...] = ()
    repair_attempts: int = 0
    outcome: str = ""
    failure_class: str = ""
    dont_rules: tuple[str, ...] = ()
    learning_ids: tuple[str, ...] = ()
    parent_digest: str = ""
    metadata: tuple[tuple[str, str], ...] = ()
    digest: str = field(default="", compare=True)

    def __post_init__(self) -> None:
        for name, value in (("episode_id", self.episode_id), ("task_id", self.task_id),
                            ("project", self.project), ("repository_snapshot", self.repository_snapshot),
                            ("intent_digest", self.intent_digest)):
            if not value.strip():
                raise ValueError(f"{name} is required")
        for name, values in (("evidence_ids", self.evidence_ids),
                             ("verification_ids", self.verification_ids),
                             ("review_ids", self.review_ids),
                             ("regression_ids", self.regression_ids),
                             ("dont_rules", self.dont_rules),
                             ("learning_ids", self.learning_ids)):
            if len(values) != len(set(values)) or any(not x.strip() for x in values):
                raise ValueError(f"{name} must contain unique non-empty values")
        if self.repair_attempts < 0:
            raise ValueError("repair_attempts cannot be negative")
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise ValueError("episode digest does not match contents")
        object.__setattr__(self, "digest", expected)

    def _payload(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id, "task_id": self.task_id,
            "project": self.project, "repository_snapshot": self.repository_snapshot,
            "intent_digest": self.intent_digest, "phase": self.phase.value,
            "plan_digest": self.plan_digest, "capability": self.capability,
            "resource_lane": self.resource_lane, "evidence_ids": list(self.evidence_ids),
            "verification_ids": list(self.verification_ids), "review_ids": list(self.review_ids),
            "regression_ids": list(self.regression_ids),
            "findings": [{"finding_id": f.finding_id, "hat": f.hat, "severity": f.severity,
                          "detail": f.detail, "evidence_ids": list(f.evidence_ids),
                          "recommendation": f.recommendation} for f in self.findings],
            "repair_attempts": self.repair_attempts, "outcome": self.outcome,
            "failure_class": self.failure_class, "dont_rules": list(self.dont_rules),
            "learning_ids": list(self.learning_ids), "parent_digest": self.parent_digest,
            "metadata": dict(self.metadata),
        }

    def as_dict(self) -> dict[str, Any]:
        value = self._payload()
        value["digest"] = self.digest
        return value

    def _next(self, **changes: Any) -> "EngineeringEpisode":
        return replace(self, **changes, parent_digest=self.digest, digest="")

    def with_plan(self, plan_digest: str, *, capability: str = "", resource_lane: str = "") -> "EngineeringEpisode":
        if not plan_digest.strip():
            raise ValueError("plan_digest is required")
        if self.phase not in {EpisodePhase.INTAKE, EpisodePhase.PLANNED}:
            raise EpisodeGateError("plan can only be attached before execution")
        return self._next(phase=EpisodePhase.PLANNED, plan_digest=plan_digest,
                          capability=capability or self.capability,
                          resource_lane=resource_lane or self.resource_lane)

    def start_execution(self) -> "EngineeringEpisode":
        if self.phase != EpisodePhase.PLANNED:
            raise EpisodeGateError("execution requires a plan")
        if not self.evidence_ids:
            raise EpisodeGateError("execution requires at least one evidence reference")
        return self._next(phase=EpisodePhase.EXECUTING)

    def add_evidence(self, *evidence_ids: str) -> "EngineeringEpisode":
        values = tuple(dict.fromkeys((*self.evidence_ids, *(str(x) for x in evidence_ids))))
        if any(not x.strip() for x in values):
            raise ValueError("evidence ids cannot be empty")
        return self._next(evidence_ids=values)

    def begin_verification(self) -> "EngineeringEpisode":
        if self.phase not in {EpisodePhase.EXECUTING, EpisodePhase.REPAIRING}:
            raise EpisodeGateError("verification requires execution or repair")
        if not self.evidence_ids:
            raise EpisodeGateError("verification requires evidence")
        return self._next(phase=EpisodePhase.VERIFYING)

    def record_verification(self, *verification_ids: str) -> "EngineeringEpisode":
        values = tuple(dict.fromkeys((*self.verification_ids, *(str(x) for x in verification_ids))))
        if not values:
            raise EpisodeGateError("at least one verification receipt is required")
        return self._next(verification_ids=values)

    def begin_review(self) -> "EngineeringEpisode":
        if self.phase != EpisodePhase.VERIFYING or not self.verification_ids:
            raise EpisodeGateError("review requires verification receipts")
        return self._next(phase=EpisodePhase.REVIEWING)

    def record_review(self, *review_ids: str, findings: Iterable[EpisodeFinding] = ()) -> "EngineeringEpisode":
        values = tuple(dict.fromkeys((*self.review_ids, *(str(x) for x in review_ids))))
        if not values:
            raise EpisodeGateError("review receipt is required")
        return self._next(review_ids=values, findings=tuple(findings))

    def repair(self, findings: Iterable[EpisodeFinding]) -> "EngineeringEpisode":
        items = tuple(findings)
        if not items:
            raise ValueError("repair requires findings")
        if self.phase != EpisodePhase.REVIEWING:
            raise EpisodeGateError("repair requires review")
        return self._next(phase=EpisodePhase.REPAIRING,
                          findings=items, repair_attempts=self.repair_attempts + 1)

    def complete(self, *, outcome: str, regression_ids: Iterable[str] = (),
                 learning_ids: Iterable[str] = ()) -> "EngineeringEpisode":
        regressions = tuple(dict.fromkeys(str(x) for x in regression_ids))
        learning = tuple(dict.fromkeys(str(x) for x in learning_ids))
        if self.phase != EpisodePhase.REVIEWING:
            raise EpisodeGateError("completion requires a review")
        if not self.verification_ids or not self.review_ids:
            raise EpisodeGateError("completion requires verification and review receipts")
        if not outcome.strip():
            raise ValueError("outcome is required")
        if not regressions:
            raise EpisodeGateError("completion requires regression evidence")
        if self.findings:
            raise EpisodeGateError("unresolved review findings must be repaired before completion")
        return self._next(phase=EpisodePhase.COMPLETED, outcome=outcome,
                          regression_ids=regressions, learning_ids=learning)

    def fail(self, *, failure_class: str, dont_rules: Iterable[str],
             outcome: str = "failed") -> "EngineeringEpisode":
        rules = tuple(dict.fromkeys(str(x).strip() for x in dont_rules if str(x).strip()))
        if self.phase not in {EpisodePhase.EXECUTING, EpisodePhase.VERIFYING,
                              EpisodePhase.REVIEWING, EpisodePhase.REPAIRING}:
            raise EpisodeGateError("failure can only be recorded during active execution")
        if not failure_class.strip() or not rules:
            raise EpisodeGateError("verified failure requires a failure class and at least one do-not rule")
        return self._next(phase=EpisodePhase.FAILED, failure_class=failure_class.strip(),
                          dont_rules=rules, outcome=outcome)

    def to_envelope(self, envelope: EngineeringEvidenceEnvelope) -> EngineeringEvidenceEnvelope:
        if envelope.task_id != self.task_id or envelope.repository_snapshot_digest != self.repository_snapshot:
            raise EpisodeGateError("episode and evidence envelope lineage do not match")
        return envelope.bind(
            decision_ids=(self.plan_digest,) if self.plan_digest else None,
            verification_ids=self.verification_ids,
            review_ids=self.review_ids,
            regression_ids=self.regression_ids,
            outcome_id=self.outcome or None,
        )

    @classmethod
    def start(cls, *, episode_id: str, task_id: str, project: str,
              repository_snapshot: str, intent_digest: str,
              evidence_ids: Iterable[str]) -> "EngineeringEpisode":
        return cls(episode_id=episode_id, task_id=task_id, project=project,
                   repository_snapshot=repository_snapshot, intent_digest=intent_digest,
                   evidence_ids=tuple(dict.fromkeys(str(x) for x in evidence_ids)))


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


__all__ = ["EngineeringEpisode", "EpisodeFinding", "EpisodeGateError", "EpisodePhase"]
