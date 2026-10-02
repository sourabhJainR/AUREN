"""Bounded canary/promotion lifecycle for learned execution modes."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .experience_router import ExperienceRouter
from .learning_steward import LearningSteward


@dataclass(frozen=True)
class ModeRollout:
    mode: str
    state: str
    samples: int
    confidence: float
    evidence_quality: float
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "state": self.state,
            "samples": self.samples,
            "confidence": round(self.confidence, 3),
            "evidence_quality": round(self.evidence_quality, 3),
            "reason": self.reason,
        }


class ExecutionModeCanaryController:
    """Gate learned modes through baseline, canary, promotion or rollback."""

    def __init__(self, root: Path, *, minimum_samples: int = 3,
                 min_confidence: float = 0.65, min_evidence: float = 0.70) -> None:
        self.root = Path(root)
        self.minimum_samples = max(1, int(minimum_samples))
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.min_evidence = max(0.0, min(1.0, float(min_evidence)))

    @staticmethod
    def _key(role: str, task: str, mode: str) -> str:
        return f"{role}:{task[:96]}:execution-mode:{mode}"

    def _last_rollout(self, mode: str) -> dict[str, object] | None:
        path = self.root / ".ai-harness" / "state" / "execution-mode-rollouts.jsonl"
        if not path.exists():
            return None
        last = None
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("mode") == mode:
                    last = row
        return last

    def evaluate(self, *, role: str, task: str, mode: str) -> ModeRollout:
        summary = ExperienceRouter(
            self.root, minimum_samples=self.minimum_samples
        ).summarize(self._key(role, task, mode))
        if summary is None:
            return ModeRollout(mode, "candidate", 0, 0.0, 0.0, "no observed evidence")
        prior = self._last_rollout(mode)
        if prior and prior.get("state") == "canary":
            latest = LearningSteward.experience_history(
                self.root, self._key(role, task, mode), limit=1
            )
            if latest:
                passed = str(latest[0].get("outcome", "")).lower() in {"worked", "passed", "success"}
                if summary.samples >= self.minimum_samples and passed and summary.evidence_quality >= self.min_evidence:
                    return ModeRollout(mode, "promoted", summary.samples, summary.confidence,
                                        summary.evidence_quality, "previous canary produced sufficient evidence")
                if not passed:
                    return ModeRollout(mode, "rollback", summary.samples, summary.confidence,
                                        summary.evidence_quality, "previous canary failed; retain baseline")
        if summary.samples < self.minimum_samples or summary.confidence < self.min_confidence:
            return ModeRollout(mode, "candidate", summary.samples, summary.confidence,
                                summary.evidence_quality, "insufficient evidence for canary")
        return ModeRollout(mode, "canary", summary.samples, summary.confidence,
                           summary.evidence_quality, "eligible for bounded canary")

    @staticmethod
    def record_state(root: Path, rollout: ModeRollout) -> None:
        path = Path(root) / ".ai-harness" / "state" / "execution-mode-rollouts.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(rollout.as_dict(), sort_keys=True) + "\n")


__all__ = ["ExecutionModeCanaryController", "ModeRollout"]
