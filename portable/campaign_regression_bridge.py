"""Bridge autonomous campaign outcomes into the governed regression corpus.

Failed campaign observations become candidate regression cases. Independent
holdout evidence can validate those cases; only the existing RegressionCorpus
promotion/retirement gates can activate or supersede them. No execution
authority is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .autonomous_campaign_learning import CampaignAttribution, CampaignLearning
from .regression_corpus import RegressionCase, RegressionCorpus
from .persistent_memory import PersistentMemory


@dataclass(frozen=True)
class RegressionBridgeResult:
    campaign_id: str
    candidate_case_ids: tuple[str, ...]
    validated_case_ids: tuple[str, ...]
    retired_case_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "campaign_id": self.campaign_id,
            "candidate_case_ids": list(self.candidate_case_ids),
            "validated_case_ids": list(self.validated_case_ids),
            "retired_case_ids": list(self.retired_case_ids),
        }


class CampaignRegressionBridge:
    """Translate verified campaign evidence into durable regression memory."""

    def __init__(self, root: Path, *, project: str = "autonomous-campaign") -> None:
        self.root = Path(root)
        self.project = project.strip() or "autonomous-campaign"
        self.memory = PersistentMemory(
            self.root / ".ai-harness" / "state" / "autonomous-regression.sqlite3",
            require_approval=False,
        )
        self.corpus = RegressionCorpus(self.memory, self.project)

    @staticmethod
    def _episode(row: CampaignAttribution, campaign_id: str) -> object:
        # RegressionCorpus accepts a small episode-shaped object; keeping this
        # adapter local avoids coupling benchmark code to an episode schema.
        class Episode:
            pass
        episode = Episode()
        episode.phase = "failed" if not row.realized_success else "completed"
        episode.evidence_ids = row.evidence_ids
        episode.capability = row.failure_class
        episode.failure_class = row.failure_class
        episode.task_id = row.task_id
        episode.intent_digest = f"{campaign_id}:{row.task_id}"
        episode.episode_id = (
            f"{campaign_id}:{row.task_id}:{row.failure_class}:"
            f"{row.predicted_success}:{row.realized_success}:{row.score_error:.6f}"
        )
        episode.dont_rules = ()
        return episode

    def ingest(self, learning: CampaignLearning) -> tuple[RegressionCase, ...]:
        rows = []
        for observation in learning.observations:
            # Persist failures and materially miscalibrated outcomes; successful
            # near-perfect predictions need not inflate the regression corpus.
            if (
                not observation.realized_success
                or observation.score_error >= 0.20
            ):
                rows.append(
                    self.corpus.ingest_episode(
                        self._episode(observation, learning.campaign_id)
                    )
                )
        return tuple(rows)

    def validate(
        self,
        learning: CampaignLearning,
        *,
        case_ids: Iterable[str] = (),
        evidence_by_case: Mapping[str, Iterable[str]] | None = None,
    ) -> tuple[RegressionCase, ...]:
        evidence_by_case = evidence_by_case or {}
        validated = []
        for case_id in case_ids:
            evidence = tuple(evidence_by_case.get(case_id, ()))
            if not evidence:
                continue
            validated.append(
                self.corpus.record_validation(
                    case_id, passed=True, evidence_ids=evidence, independent=True
                )
            )
        return tuple(validated)

    def close_superseded(
        self,
        *,
        old_case_id: str,
        replacement_case_id: str,
        evidence_ids: Iterable[str],
    ) -> RegressionCase:
        return self.corpus.retire(
            old_case_id,
            replacement_case_id=replacement_case_id,
            evidence_ids=evidence_ids,
        )

    def retire_if_validated_replacement(
        self,
        *,
        old_case_id: str,
        replacement_case_id: str,
        holdout_evidence_ids: Iterable[str],
    ) -> RegressionCase:
        """Retire only when the replacement is already active and evidence is fresh."""
        evidence = tuple(dict.fromkeys(str(x).strip() for x in holdout_evidence_ids if str(x).strip()))
        if not evidence:
            raise ValueError("successful holdout evidence is required")
        replacement = self.corpus.get(replacement_case_id)
        if replacement.status != "active":
            raise ValueError("replacement regression must be active before retirement")
        if set(evidence) & set(replacement.evidence_ids):
            raise ValueError("retirement evidence must be fresh holdout evidence")
        return self.corpus.retire(
            old_case_id,
            replacement_case_id=replacement_case_id,
            evidence_ids=evidence,
        )

    def apply(
        self,
        learning: CampaignLearning,
        *,
        validation_case_ids: Iterable[str] = (),
        evidence_by_case: Mapping[str, Iterable[str]] | None = None,
    ) -> RegressionBridgeResult:
        candidates = self.ingest(learning)
        validated = self.validate(
            learning,
            case_ids=validation_case_ids,
            evidence_by_case=evidence_by_case,
        )
        return RegressionBridgeResult(
            learning.campaign_id,
            tuple(x.case_id for x in candidates),
            tuple(x.case_id for x in validated if x.status == "active"),
            (),
        )


__all__ = ["CampaignRegressionBridge", "RegressionBridgeResult"]
