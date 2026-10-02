"""Context-specific outcome learning for execution decisions.

Learning is indexed by a stable workload-context fingerprint. Exact-context
evidence is preferred; global role/task evidence remains the bounded fallback
for cold-start and sparse contexts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .decision_context import DecisionContext
from .experience_router import ExperienceRouter
from .learning_steward import LearningSteward
from runtime.task_memory import approach_history


@dataclass(frozen=True)
class ContextPolicySelection:
    strategy: str
    mode: str
    learned: bool
    confidence: float
    samples: int
    rationale: str
    evidence_scope: str = "none"
    similarity: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {"strategy": self.strategy, "mode": self.mode,
                "learned": self.learned, "confidence": round(self.confidence, 3),
                "samples": self.samples, "rationale": self.rationale,
                "evidence_scope": self.evidence_scope, "similarity": round(self.similarity, 3)}


class ContextSpecificDecisionLearner:
    """Learn policy outcomes conditional on the current workload context."""

    def __init__(self, root: Path, *, minimum_samples: int = 3,
                 min_confidence: float = 0.65, min_margin: float = 0.04) -> None:
        self.root = Path(root)
        self.minimum_samples = max(1, int(minimum_samples))
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.min_margin = max(0.0, float(min_margin))

    @staticmethod
    def _key(role: str, task: str, context: DecisionContext, strategy: str, mode: str) -> str:
        return f"{role}:{task[:96]}:decision-context:{context.fingerprint}:{strategy}:{mode}"

    @staticmethod
    def _score(summary) -> float:
        return (0.45 * summary.evidence_quality
                + 0.30 * summary.success_rate
                + 0.15 * (1.0 - max(0.0, min(1.0, summary.avg_cost)))
                + 0.10 * (1.0 - max(0.0, min(1.0, summary.failure_rate))))

    @staticmethod
    def _base_prefix(role: str, task: str) -> str:
        return f"{role}:{task[:96]}:decision-context:"

    @classmethod
    def _similar_history(cls, root: Path, *, role: str, task: str,
                         context: DecisionContext, strategy: str, mode: str,
                         min_similarity: float, limit: int = 120):
        """Aggregate bounded evidence from nearby contexts for one policy pair."""
        prefix = cls._base_prefix(role, task)
        suffix = f":{strategy}:{mode}"
        rows = approach_history(root, prefix, limit=max(1, int(limit)), exact=False)
        weighted = []
        for row in rows:
            approach = str(row.get("approach", ""))
            if not approach.startswith(prefix) or not approach.endswith(suffix):
                continue
            try:
                payload = json.loads(str(row.get("detail", "{}")))
                decision = payload.get("decision", {})
                if isinstance(decision, str):
                    decision = json.loads(decision)
                source = decision.get("context", {}) if isinstance(decision, dict) else {}
                source_context = DecisionContext(
                    float(source["complexity"]), float(source["dependency_parallelism"]),
                    float(source["resource_pressure"]), float(source["latency_pressure"]),
                    float(source["evidence_value"]), float(source["failure_risk"]),
                )
                similarity = context.similarity(source_context)
                if similarity < min_similarity:
                    continue
                outcome = str(row.get("outcome", "")).lower()
                if outcome not in {"worked", "passed", "success", "failed", "regressed", "blocked"}:
                    continue
                evidence = max(0.0, min(1.0, float(payload.get("evidence_quality", 0.0))))
                cost = max(0.0, min(1.0, float(payload.get("cost_score", 1.0))))
                duration = max(0.0, float(payload.get("duration_seconds", 0.0)))
                weighted.append((similarity, outcome, evidence, cost, duration))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        if not weighted:
            return None, 0.0
        class Summary:
            pass
        summary = Summary()
        total = sum(x[0] for x in weighted)
        success = sum(x[0] for x in weighted if x[1] in {"worked", "passed", "success"})
        failure = sum(x[0] for x in weighted if x[1] in {"failed", "regressed", "blocked"})
        if success + failure <= 0:
            return None, 0.0
        summary.samples = len(weighted)
        summary.success_rate = (success + 1.0) / (success + failure + 2.0)
        summary.failure_rate = (failure + 1.0) / (success + failure + 2.0)
        summary.evidence_quality = sum(x[0] * x[2] for x in weighted) / total
        summary.avg_cost = sum(x[0] * x[3] for x in weighted) / total
        summary.avg_latency = sum(x[0] * x[4] for x in weighted) / total
        effective_samples = success + failure
        mean_similarity = total / len(weighted)
        summary.confidence = min(1.0, effective_samples / 8.0 * mean_similarity)
        return summary, max(x[0] for x in weighted)

    def select(self, *, role: str, task: str, context: DecisionContext,
               baseline_strategy: str = "default",
               baseline_mode: str = "balanced",
               candidates: tuple[tuple[str, str], ...] = (),
               min_similarity: float = 0.82) -> ContextPolicySelection:
        pairs = candidates or tuple(
            (strategy, mode)
            for strategy in ("default", "evidence-first", "deep-verify", "fast-path")
            for mode in ("balanced", "parallel", "serial", "verify-heavy")
        )
        router = ExperienceRouter(self.root, minimum_samples=self.minimum_samples)

        exact = []
        for strategy, mode in pairs:
            summary = router.summarize(self._key(role, task, context, strategy, mode))
            if summary is not None and summary.samples >= self.minimum_samples and summary.confidence >= self.min_confidence:
                exact.append((self._score(summary), summary.confidence, strategy, mode, summary.samples))
        baseline_summary = router.summarize(self._key(role, task, context, baseline_strategy, baseline_mode))
        baseline_score = (
            self._score(baseline_summary)
            if baseline_summary is not None
            and baseline_summary.samples >= self.minimum_samples
            and baseline_summary.confidence >= self.min_confidence
            else None
        )

        if exact:
            exact.sort(key=lambda x: (-x[0], -x[1], x[2], x[3]))
            score, confidence, strategy, mode, samples = exact[0]
            if (strategy, mode) == (baseline_strategy, baseline_mode):
                return ContextPolicySelection(strategy, mode, False, confidence, samples,
                                              "exact context evidence supports the baseline", "exact", 1.0)
            if baseline_score is not None and score < baseline_score + self.min_margin:
                return ContextPolicySelection(
                    baseline_strategy, baseline_mode, False, baseline_summary.confidence,
                    baseline_summary.samples,
                    f"exact-context candidate {strategy}+{mode} did not beat baseline by {self.min_margin:.2f}",
                    "exact", 1.0)
            return ContextPolicySelection(strategy, mode, True, confidence, samples,
                                          f"exact-context evidence selected {strategy}+{mode} with bounded score {score:.3f}",
                                          "exact", 1.0)

        threshold = max(0.5, min(0.99, float(min_similarity)))
        similar = []
        for strategy, mode in pairs:
            summary, similarity = self._similar_history(
                self.root, role=role, task=task, context=context,
                strategy=strategy, mode=mode, min_similarity=threshold)
            if summary is None or summary.samples < self.minimum_samples or summary.confidence < self.min_confidence:
                continue
            similar.append((self._score(summary), summary.confidence, strategy, mode, summary.samples, similarity))

        if not similar:
            return ContextPolicySelection(
                baseline_strategy, baseline_mode, False, 0.0, 0,
                "no exact or sufficiently similar context history meets the confidence gate",
                "none", 0.0)

        similar.sort(key=lambda x: (-x[0], -x[5], -x[1], x[2], x[3]))
        score, confidence, strategy, mode, samples, similarity = similar[0]
        baseline_similar, baseline_similarity = self._similar_history(
            self.root, role=role, task=task, context=context,
            strategy=baseline_strategy, mode=baseline_mode, min_similarity=threshold)
        baseline_similar_score = self._score(baseline_similar) if (
            baseline_similar is not None
            and baseline_similar.samples >= self.minimum_samples
            and baseline_similar.confidence >= self.min_confidence
        ) else None
        if (strategy, mode) == (baseline_strategy, baseline_mode):
            return ContextPolicySelection(strategy, mode, False, confidence, samples,
                                          "similar-context evidence supports the baseline", "similar", similarity)
        if baseline_similar_score is not None and score < baseline_similar_score + self.min_margin:
            return ContextPolicySelection(
                baseline_strategy, baseline_mode, False,
                baseline_similar.confidence, baseline_similar.samples,
                f"similar-context candidate {strategy}+{mode} did not beat baseline by {self.min_margin:.2f}",
                "similar", baseline_similarity)
        return ContextPolicySelection(
            strategy, mode, True, confidence, samples,
            f"similar-context evidence selected {strategy}+{mode} at similarity {similarity:.3f}",
            "similar", similarity)

    def record(self, *, role: str, task: str, context: DecisionContext,
               strategy: str, mode: str, outcome: str,
               evidence_quality: float, cost_score: float,
               duration_seconds: float, evidence_ids=()) -> None:
        LearningSteward(self.root, run_id="decision-context-learning", task=task).record_experience(
            key=self._key(role, task, context, strategy, mode),
            outcome=outcome,
            evidence_quality=evidence_quality,
            cost_score=cost_score,
            duration_seconds=duration_seconds,
            decision=json.dumps({"context": context.as_dict(), "strategy": strategy, "mode": mode},
                                sort_keys=True),
            evidence_ids=list(evidence_ids),
        )


__all__ = ["ContextPolicySelection", "ContextSpecificDecisionLearner"]
