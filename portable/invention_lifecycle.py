"""Evidence-backed lifecycle for failure-driven capability inventions.

Binds an invention proposal to a reusable capability pattern, requires explicit
unseen holdout provenance, evaluates transfer evidence, and delegates promotion
/ retirement to the existing capability graduation gates. This module is
advisory: it never executes an invention or changes execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .capability_graduation import CapabilityGraduationController, CapabilityRollout
from .cross_task_capability_abstraction import CapabilityPattern, TransferValidation
from .failure_cluster_invention import CapabilityInvention
from .learning_steward import LearningSteward


@dataclass(frozen=True)
class InventionHoldoutRequest:
    invention_id: str
    pattern_id: str
    hypothesis: str
    preconditions: tuple[str, ...]
    trigger_evidence: tuple[str, ...]
    holdout_ids: tuple[str, ...]
    provenance: str

    def as_dict(self) -> dict[str, object]:
        return {
            "invention_id": self.invention_id,
            "pattern_id": self.pattern_id,
            "hypothesis": self.hypothesis,
            "preconditions": list(self.preconditions),
            "trigger_evidence": list(self.trigger_evidence),
            "holdout_ids": list(self.holdout_ids),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class InventionLifecycleResult:
    invention_id: str
    pattern_id: str
    state: str
    rollout: CapabilityRollout
    holdout_request: InventionHoldoutRequest
    validations: tuple[TransferValidation, ...]
    benchmark_delta: float
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "invention_id": self.invention_id,
            "pattern_id": self.pattern_id,
            "state": self.state,
            "rollout": self.rollout.as_dict(),
            "holdout_request": self.holdout_request.as_dict(),
            "validations": [row.as_dict() for row in self.validations],
            "benchmark_delta": round(self.benchmark_delta, 3),
            "reason": self.reason,
        }


class EvidenceBackedInventionLifecycle:
    """Connect invention proposals to independent transfer and graduation gates."""

    def __init__(
        self,
        root: Path,
        *,
        minimum_holdouts: int = 2,
        minimum_uplift: float = 0.03,
        minimum_benchmark_delta: float = 0.03,
    ) -> None:
        self.root = Path(root)
        self.minimum_holdouts = max(1, int(minimum_holdouts))
        self.minimum_uplift = max(0.0, min(1.0, float(minimum_uplift)))
        self.minimum_benchmark_delta = max(-1.0, min(1.0, float(minimum_benchmark_delta)))

    @staticmethod
    def _provenance(
        invention: CapabilityInvention,
        pattern: CapabilityPattern,
        trigger_evidence: Sequence[str],
        holdout_ids: Sequence[str],
    ) -> str:
        raw = json.dumps(
            {
                "invention": invention.invention_id,
                "pattern": pattern.pattern_id,
                "trigger_evidence": list(trigger_evidence),
                "holdouts": list(holdout_ids),
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def build_holdout_request(
        self,
        invention: CapabilityInvention,
        pattern: CapabilityPattern,
        *,
        trigger_evidence: Iterable[str],
        holdout_ids: Iterable[str],
    ) -> InventionHoldoutRequest:
        trigger = tuple(dict.fromkeys(str(x).strip() for x in trigger_evidence if str(x).strip()))
        holdouts = tuple(dict.fromkeys(str(x).strip() for x in holdout_ids if str(x).strip()))
        if len(holdouts) < self.minimum_holdouts:
            raise ValueError("insufficient independent holdout ids")
        if set(trigger) & set(holdouts):
            raise ValueError("holdout ids overlap invention trigger evidence")
        if not pattern.pattern_id.strip():
            raise ValueError("pattern id is required")
        hypothesis = (
            f"under {pattern.context_signature}, composing "
            f"{', '.join(invention.components)} should improve {invention.expected_effect}"
        )
        provenance = self._provenance(invention, pattern, trigger, holdouts)
        return InventionHoldoutRequest(
            invention.invention_id,
            pattern.pattern_id,
            hypothesis,
            pattern.preconditions,
            trigger,
            holdouts,
            provenance,
        )

    def evaluate(
        self,
        invention: CapabilityInvention,
        pattern: CapabilityPattern,
        validations: Iterable[TransferValidation],
        *,
        trigger_evidence: Iterable[str] = (),
        holdout_ids: Iterable[str] = (),
        benchmark_before: float = 0.0,
        benchmark_after: float = 0.0,
        graduation: CapabilityGraduationController | None = None,
        role: str = "team",
    ) -> InventionLifecycleResult:
        request = self.build_holdout_request(
            invention,
            pattern,
            trigger_evidence=trigger_evidence,
            holdout_ids=holdout_ids,
        )
        rows = tuple(row for row in validations if row.pattern_id == pattern.pattern_id)
        holdout_set = set(request.holdout_ids)
        trigger_set = set(request.trigger_evidence)
        if not rows:
            rollout = CapabilityGraduationController(
                minimum_cohorts=self.minimum_holdouts
            ).evaluate(pattern, ())
            state = "candidate"
            reason = "no independent transfer validation supplied"
        else:
            valid = []
            for row in rows:
                if set(row.evidence_ids) & trigger_set:
                    continue
                if row.evidence_ids and set(row.evidence_ids) <= holdout_set:
                    valid.append(row)
            controller = graduation or CapabilityGraduationController(
                minimum_cohorts=self.minimum_holdouts
            )
            rollout = controller.evaluate(pattern, valid)
            state = rollout.state
            reason = rollout.reason

        delta = float(benchmark_after) - float(benchmark_before)
        if state == "promoted" and delta < self.minimum_benchmark_delta:
            state = "candidate"
            reason = (f"benchmark improvement gate not met: delta={delta:.3f} "
                      f"< required={self.minimum_benchmark_delta:.3f}")
        result = InventionLifecycleResult(
            invention.invention_id,
            pattern.pattern_id,
            state,
            rollout,
            request,
            tuple(valid if rows else ()),
            delta,
            reason,
        )
        LearningSteward(
            self.root, run_id="invention-lifecycle", task=invention.trigger
        ).record_experience(
            key=f"{role}:invention-lifecycle:{invention.invention_id}:{pattern.pattern_id}",
            outcome=state,
            evidence_quality=rollout.confidence,
            cost_score=0.0,
            duration_seconds=0.0,
            decision=json.dumps(
                {
                    "state": state,
                    "benchmark_delta": delta,
                    "provenance": request.provenance,
                    "rollout": rollout.as_dict(),
                },
                sort_keys=True,
            ),
            evidence_ids=request.trigger_evidence
            + tuple(
                eid
                for row in result.validations
                for eid in row.evidence_ids
            ),
        )
        return result


__all__ = [
    "EvidenceBackedInventionLifecycle",
    "InventionHoldoutRequest",
    "InventionLifecycleResult",
]
