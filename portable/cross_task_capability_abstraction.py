"""Provider-free cross-task capability abstraction and transfer validation.

Extracts reusable decision patterns from verified episodes, validates them on
held-out tasks, and exposes only evidence-backed promotion candidates. This
module is advisory: it never executes or changes authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping

from .learning_steward import LearningSteward
from runtime.task_memory import approach_history


@dataclass(frozen=True)
class CapabilityPattern:
    pattern_id: str
    action: str
    mode: str
    context_signature: str
    preconditions: tuple[str, ...]
    expected_effect: str
    train_samples: int
    train_success_rate: float
    train_evidence_quality: float
    state: str = "candidate"

    def as_dict(self) -> dict[str, object]:
        return {
            "pattern_id": self.pattern_id,
            "action": self.action,
            "mode": self.mode,
            "context_signature": self.context_signature,
            "preconditions": list(self.preconditions),
            "expected_effect": self.expected_effect,
            "train_samples": self.train_samples,
            "train_success_rate": round(self.train_success_rate, 3),
            "train_evidence_quality": round(self.train_evidence_quality, 3),
            "state": self.state,
        }


@dataclass(frozen=True)
class TransferValidation:
    pattern_id: str
    holdout_samples: int
    candidate_score: float
    baseline_score: float
    uplift: float
    regression_passed: bool
    verified: bool
    promoted: bool
    evidence_ids: tuple[str, ...] = ()
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "pattern_id": self.pattern_id,
            "holdout_samples": self.holdout_samples,
            "candidate_score": round(self.candidate_score, 3),
            "baseline_score": round(self.baseline_score, 3),
            "uplift": round(self.uplift, 3),
            "regression_passed": self.regression_passed,
            "verified": self.verified,
            "promoted": self.promoted,
            "evidence_ids": list(self.evidence_ids),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CapabilityHypothesis:
    hypothesis_id: str
    components: tuple[str, ...]
    trigger: str
    expected_effect: str
    information_gain: float
    state: str = "candidate"

    def as_dict(self) -> dict[str, object]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "components": list(self.components),
            "trigger": self.trigger,
            "expected_effect": self.expected_effect,
            "information_gain": round(self.information_gain, 3),
            "state": self.state,
        }


class CrossTaskCapabilityAbstraction:
    """Learn transferable patterns without embeddings, models, or new deps."""

    def __init__(self, root: Path, *, minimum_train_samples: int = 4,
                 minimum_success: float = 0.70, minimum_evidence: float = 0.70,
                 minimum_uplift: float = 0.03, regression_tolerance: float = 0.03) -> None:
        self.root = Path(root)
        self.minimum_train_samples = max(2, int(minimum_train_samples))
        self.minimum_success = max(0.0, min(1.0, float(minimum_success)))
        self.minimum_evidence = max(0.0, min(1.0, float(minimum_evidence)))
        self.minimum_uplift = max(0.0, min(1.0, float(minimum_uplift)))
        self.regression_tolerance = max(0.0, min(1.0, float(regression_tolerance)))

    @staticmethod
    def _band(value: float) -> str:
        value = max(0.0, min(1.0, float(value)))
        return "low" if value < 0.34 else ("medium" if value < 0.67 else "high")

    @classmethod
    def context_signature(cls, context: Mapping[str, object]) -> str:
        keys = ("complexity", "dependency_parallelism", "resource_pressure",
                "latency_pressure", "evidence_value", "failure_risk")
        return "|".join(f"{key}={cls._band(float(context.get(key, 0.5)))}" for key in keys)

    @staticmethod
    def _id(action: str, mode: str, signature: str) -> str:
        raw = f"{action}|{mode}|{signature}".encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    @classmethod
    def _episode(cls, row: Mapping[str, object]) -> dict[str, object] | None:
        try:
            detail = json.loads(str(row.get("detail", "{}")))
            decision = detail.get("decision", {})
            if isinstance(decision, str):
                decision = json.loads(decision)
            context = decision.get("context", {})
            action = str(decision.get("strategy", "")).strip()
            mode = str(decision.get("mode", "")).strip()
            outcome = str(row.get("outcome", "")).lower()
            if not action or not mode or outcome not in {"worked", "failed", "regressed"}:
                return None
            return {
                "task": str(row.get("task", "")),
                "action": action,
                "mode": mode,
                "context_signature": cls.context_signature(context),
                "success": outcome == "worked",
                "evidence_quality": max(0.0, min(1.0, float(detail.get("evidence_quality", 0.0)))),
                "evidence_ids": tuple(str(x) for x in row.get("evidence_ids", []) if str(x)),
            }
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            return None

    def discover(self, *, role: str = "team", limit: int = 240) -> tuple[CapabilityPattern, ...]:
        rows = approach_history(self.root, f"{role}:abstract-episode:", limit=max(1, int(limit)), exact=False)
        grouped: dict[tuple[str, str, str], list[dict[str, object]]] = {}
        for row in rows:
            item = self._episode(row)
            if item is None:
                continue
            grouped.setdefault((str(item["action"]), str(item["mode"]), str(item["context_signature"])), []).append(item)
        patterns = []
        for (action, mode, signature), items in sorted(grouped.items()):
            successes = sum(bool(x["success"]) for x in items)
            success_rate = successes / len(items)
            evidence = sum(float(x["evidence_quality"]) for x in items) / len(items)
            if len(items) < self.minimum_train_samples or success_rate < self.minimum_success or evidence < self.minimum_evidence:
                continue
            pid = self._id(action, mode, signature)
            patterns.append(CapabilityPattern(
                pid, action, mode, signature,
                ("verified-outcome", "matching-context-profile"),
                f"improve verified execution evidence using {action}+{mode}",
                len(items), success_rate, evidence,
            ))
        return tuple(patterns)

    def validate(self, pattern: CapabilityPattern, holdouts: Iterable[Mapping[str, object]]) -> TransferValidation:
        rows = [dict(x) for x in holdouts]
        if not rows:
            return TransferValidation(pattern.pattern_id, 0, 0.0, 0.0, 0.0, False, False, False,
                                      reason="no unseen holdout episodes supplied")
        usable = [x for x in rows if self._episode(x) is not None]
        if not usable:
            return TransferValidation(pattern.pattern_id, 0, 0.0, 0.0, 0.0, False, False, False,
                                      reason="holdouts contain no verified episode evidence")
        candidate = []
        baseline = []
        evidence_ids = []
        for row in usable:
            item = self._episode(row)
            assert item is not None
            if str(item["context_signature"]) != pattern.context_signature:
                continue
            score = (0.7 if bool(item["success"]) else 0.1) + 0.3 * float(item["evidence_quality"])
            if str(item["action"]) == pattern.action and str(item["mode"]) == pattern.mode:
                candidate.append(score)
                evidence_ids.extend(item["evidence_ids"])
            else:
                baseline.append(score)
        if not candidate or not baseline:
            return TransferValidation(pattern.pattern_id, len(candidate), sum(candidate) / max(1, len(candidate)),
                                      sum(baseline) / max(1, len(baseline)), 0.0, False, False, False,
                                      tuple(dict.fromkeys(evidence_ids)),
                                      "holdouts need both candidate and incumbent observations")
        candidate_score = sum(candidate) / len(candidate)
        baseline_score = sum(baseline) / len(baseline)
        uplift = candidate_score - baseline_score
        regression = min(candidate) + self.regression_tolerance >= min(baseline)
        verified = len(evidence_ids) > 0
        promoted = verified and regression and uplift >= self.minimum_uplift
        return TransferValidation(
            pattern.pattern_id, len(candidate), candidate_score, baseline_score, uplift,
            regression, verified, promoted, tuple(dict.fromkeys(evidence_ids)),
            "cross-task holdout promotion gate passed" if promoted else "cross-task transfer gate not met",
        )

    def synthesize_hypotheses(self, patterns: Iterable[CapabilityPattern], *, limit: int = 4) -> tuple[CapabilityHypothesis, ...]:
        rows = tuple(patterns)
        if len(rows) < 2:
            return ()
        hypotheses = []
        for left, right in zip(rows, rows[1:]):
            if left.context_signature != right.context_signature:
                continue
            components = tuple(dict.fromkeys((left.action, left.mode, right.action, right.mode)))
            if len(components) < 2:
                continue
            raw = "|".join(components) + "|" + left.context_signature
            hypotheses.append(CapabilityHypothesis(
                hashlib.sha256(raw.encode()).hexdigest()[:16],
                components,
                left.context_signature,
                "compose independently verified patterns when one pathway is insufficient",
                min(1.0, (left.train_evidence_quality + right.train_evidence_quality) / 2.0),
            ))
            if len(hypotheses) >= max(1, int(limit)):
                break
        return tuple(hypotheses)

    def record_episode(self, *, role: str, task: str, strategy: str, mode: str,
                       context: Mapping[str, object], outcome: str,
                       evidence_quality: float, evidence_ids: Iterable[str] = ()) -> None:
        key = f"{role}:abstract-episode:{self.context_signature(context)}:{strategy}:{mode}"
        LearningSteward(self.root, run_id="cross-task-abstraction", task=task).record_experience(
            key=key, outcome=outcome, evidence_quality=evidence_quality,
            cost_score=0.5, duration_seconds=0.0,
            decision=json.dumps({"strategy": strategy, "mode": mode, "context": dict(context)}, sort_keys=True),
            evidence_ids=evidence_ids,
        )


__all__ = [
    "CapabilityHypothesis", "CapabilityPattern", "CrossTaskCapabilityAbstraction",
    "TransferValidation",
]
