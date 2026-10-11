"""Reversible capability graduation with bounded canary and rollback.

Promotion is blocked unless a machine-checkable decision record is valid for
trusted repository/head context, canonical evidence, an authorized approver,
and an explicit rollback plan. Every canary observation must also resolve to
the trusted evidence registry. Canary observations are retained when blocked.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from dataclasses import dataclass
from typing import Iterable, Mapping

from .decision_gate import PROMOTION_LEVELS, validate_decision_record
from .persistent_memory import PersistentMemory


@dataclass(frozen=True)
class CapabilityLifecycleReceipt:
    capability_id: str
    state: str
    baseline_score: float
    canary_count: int
    successful_canaries: int
    reasons: tuple[str, ...] = ()


class CapabilityLifecycle:
    """Bounded canary lifecycle with an evidence-backed promotion gate."""

    def __init__(
        self,
        memory: PersistentMemory,
        project: str,
        *,
        min_canaries: int = 3,
        regression_tolerance: float = 0.02,
        promotion_record: Mapping[str, object] | None = None,
        expected_repository: str | None = None,
        expected_head: str | None = None,
        authorized_approvers: Iterable[str] = (),
        known_evidence_ids: Iterable[str] | None = None,
    ) -> None:
        if not isinstance(memory, PersistentMemory):
            raise TypeError("memory must be a PersistentMemory instance")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project is required")
        if type(min_canaries) is not int or min_canaries < 1:
            raise ValueError("min_canaries must be a positive integer")
        if isinstance(regression_tolerance, bool) or not isinstance(regression_tolerance, (int, float)) or not math.isfinite(regression_tolerance) or not 0 <= regression_tolerance <= 1:
            raise ValueError("regression_tolerance must be a finite number between 0 and 1")
        for name, value in (("expected_repository", expected_repository), ("expected_head", expected_head)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string when supplied")
        if isinstance(authorized_approvers, str):
            raise ValueError("authorized_approvers must be an iterable of principal ids, not a string")
        approvers = tuple(authorized_approvers)
        if any(not isinstance(value, str) or not value.strip() for value in approvers):
            raise ValueError("authorized approver ids must be non-empty strings")
        if isinstance(known_evidence_ids, str):
            raise ValueError("known_evidence_ids must be an iterable of ids, not a string")
        evidence_ids = tuple(known_evidence_ids) if known_evidence_ids is not None else None
        if evidence_ids is not None and any(not isinstance(value, str) or not value.strip() for value in evidence_ids):
            raise ValueError("known evidence ids must be non-empty strings")

        self.memory = memory
        self.project = project.strip()
        self.min_canaries = min_canaries
        self.regression_tolerance = float(regression_tolerance)
        self.promotion_record = deepcopy(dict(promotion_record)) if promotion_record is not None else None
        self.expected_repository = expected_repository
        self.expected_head = expected_head
        self.authorized_approvers = frozenset(approvers)
        self.known_evidence_ids = frozenset(evidence_ids) if evidence_ids is not None else None

    def _rows(self, capability_id: str) -> list[dict]:
        rows = self.memory.search(self.project, capability_id, limit=256)
        result = []
        for row in rows:
            if row.category != "capability-lifecycle":
                continue
            try:
                item = json.loads(row.text)
            except json.JSONDecodeError:
                continue
            if item.get("capability_id") == capability_id:
                result.append(item)
        return result

    def _receipt(self, capability_id: str, baseline: float, rows: list[dict], state: str, reasons=()):
        canaries = [r for r in rows if r.get("event") == "canary"]
        successful = sum(
            1 for r in canaries
            if r.get("safe") is True and float(r.get("score", 0)) + self.regression_tolerance >= baseline
        )
        return CapabilityLifecycleReceipt(
            capability_id, state, baseline, len(canaries), successful, tuple(reasons)
        )

    def _promotion_errors(self, canary_evidence_ids: Iterable[str] = ()) -> list[str]:
        if self.promotion_record is None:
            return ["promotion blocked: decision record is required"]
        if not self.expected_repository or not self.expected_head:
            return ["promotion blocked: trusted repository and candidate-head context are required"]
        record = self.promotion_record
        promotion = record.get("promotion")
        level = promotion.get("level") if isinstance(promotion, Mapping) else None
        if level not in PROMOTION_LEVELS or PROMOTION_LEVELS[level] < PROMOTION_LEVELS["verified_for_scope"]:
            return ["promotion blocked: decision record must request verified_for_scope or higher"]

        errors = validate_decision_record(
            record,
            expected_repository=self.expected_repository,
            expected_head=self.expected_head,
            authorized_approvers=self.authorized_approvers,
            known_evidence_ids=self.known_evidence_ids,
        )
        if self.known_evidence_ids is None:
            errors.append("promotion blocked: trusted canonical evidence registry is required for canary observations")
        else:
            for evidence_id in canary_evidence_ids:
                if evidence_id not in self.known_evidence_ids:
                    errors.append(
                        f"promotion blocked: canary evidence_id {evidence_id!r} is not in trusted canonical evidence set"
                    )
        return errors

    def begin(self, capability_id: str, *, baseline_score: float) -> CapabilityLifecycleReceipt:
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise ValueError("capability_id is required")
        if isinstance(baseline_score, bool) or not isinstance(baseline_score, (int, float)) or not math.isfinite(baseline_score) or not 0 <= baseline_score <= 1:
            raise ValueError("baseline_score must be a finite number between 0 and 1")
        existing = self._rows(capability_id)
        if any(r.get("event") == "begin" for r in existing):
            raise ValueError("capability lifecycle already exists")
        self.memory.remember(
            self.project,
            "capability-lifecycle",
            json.dumps({
                "event": "begin",
                "capability_id": capability_id.strip(),
                "baseline_score": baseline_score,
            }, sort_keys=True),
            confidence=1.0,
            verified=True,
            approved=True,
        )
        return self._receipt(capability_id.strip(), baseline_score, [], "canary")

    def record_canary(
        self,
        capability_id: str,
        *,
        score: float,
        evidence_id: str,
        safe: bool = True,
        metadata: Mapping[str, object] | None = None,
    ) -> CapabilityLifecycleReceipt:
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("score must be a finite number between 0 and 1")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError("evidence_id is required")
        if type(safe) is not bool:
            raise ValueError("safe must be a boolean")
        rows = self._rows(capability_id)
        begins = [r for r in rows if r.get("event") == "begin"]
        if not begins:
            raise KeyError("capability lifecycle has not started")
        baseline = float(begins[-1]["baseline_score"])
        prior = [r for r in rows if r.get("event") == "canary"]
        if any(r.get("evidence_id") == evidence_id for r in prior):
            raise ValueError("evidence_id already recorded")
        regression = score + self.regression_tolerance < baseline
        state = "rolled_back" if (not safe or regression) else "canary"
        reason = "safety gate failed" if not safe else "canary regressed baseline" if regression else ""
        payload = {
            "event": "canary",
            "capability_id": capability_id.strip(),
            "score": score,
            "evidence_id": evidence_id.strip(),
            "safe": safe,
            "metadata": dict(metadata or {}),
        }
        self.memory.remember(
            self.project,
            "capability-lifecycle",
            json.dumps(payload, sort_keys=True, default=str),
            confidence=1.0 if safe else 0.0,
            verified=True,
            approved=True,
        )
        rows = self._rows(capability_id)
        canaries = [r for r in rows if r.get("event") == "canary"]
        if state != "rolled_back" and len(canaries) >= self.min_canaries:
            errors = self._promotion_errors(r.get("evidence_id", "") for r in canaries)
            if errors:
                state = "promotion_blocked"
                reason = "; ".join(errors)
            else:
                state = "promoted"
                reason = "bounded canary window and decision-record gate passed"
        return self._receipt(capability_id.strip(), baseline, rows, state, (reason,) if reason else ())

    def status(self, capability_id: str) -> CapabilityLifecycleReceipt:
        rows = self._rows(capability_id)
        begins = [r for r in rows if r.get("event") == "begin"]
        if not begins:
            raise KeyError("capability lifecycle has not started")
        baseline = float(begins[-1]["baseline_score"])
        canaries = [r for r in rows if r.get("event") == "canary"]
        if any(r.get("safe") is not True or float(r.get("score", 0)) + self.regression_tolerance < baseline for r in canaries):
            state = "rolled_back"
        elif len(canaries) >= self.min_canaries:
            errors = self._promotion_errors(r.get("evidence_id", "") for r in canaries)
            state = "promoted" if not errors else "promotion_blocked"
        else:
            state = "canary"
        errors = self._promotion_errors(r.get("evidence_id", "") for r in canaries) if state == "promotion_blocked" else []
        return self._receipt(capability_id.strip(), baseline, rows, state, errors)


__all__ = ["CapabilityLifecycle", "CapabilityLifecycleReceipt"]
