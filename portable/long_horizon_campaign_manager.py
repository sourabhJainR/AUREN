"""Bounded long-horizon campaign manager with replanning and recovery.

The manager coordinates multi-step goals through injected execution and
verification callbacks. It checkpoints every step, replans from verified
failures, enforces hard step/resource budgets, and never treats an unverified
step as completed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib, json
from typing import Any, Callable, Sequence


@dataclass(frozen=True, slots=True)
class CampaignStep:
    step_id: str
    objective: str
    budget_units: int = 1

    def __post_init__(self) -> None:
        if not self.step_id.strip() or not self.objective.strip():
            raise ValueError("step_id and objective are required")
        if self.budget_units < 1:
            raise ValueError("budget_units must be positive")


@dataclass(frozen=True, slots=True)
class CampaignCheckpoint:
    campaign_id: str
    completed_step_ids: tuple[str, ...]
    failed_step_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    remaining_budget: int
    interrupted: bool
    checkpoint_digest: str


@dataclass(frozen=True, slots=True)
class LongHorizonCampaignResult:
    campaign_id: str
    completed_step_ids: tuple[str, ...]
    failed_step_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    replans: int
    interrupted: bool
    accepted: bool
    checkpoint: CampaignCheckpoint
    result_digest: str


class LongHorizonCampaignManager:
    """Run a bounded plan while preserving verified progress and recovery."""

    def __init__(self, *, max_steps: int = 32, budget_units: int = 128, max_replans: int = 8) -> None:
        if max_steps < 1 or budget_units < 1 or max_replans < 0:
            raise ValueError("campaign limits must be valid")
        self.max_steps, self.budget_units, self.max_replans = max_steps, budget_units, max_replans

    @staticmethod
    def _digest(value: object) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    def run(
        self,
        campaign_id: str,
        steps: Sequence[CampaignStep],
        *,
        execute: Callable[[CampaignStep], Any],
        verify: Callable[[CampaignStep, Any], tuple[bool, tuple[str, ...]]],
        replan: Callable[[CampaignStep, Exception | None], Sequence[CampaignStep]] | None = None,
    ) -> LongHorizonCampaignResult:
        if not campaign_id.strip():
            raise ValueError("campaign_id is required")
        initial=tuple(steps)
        if not initial or len(initial) > self.max_steps:
            raise ValueError("step count exceeds campaign bounds")
        if len({s.step_id for s in initial}) != len(initial):
            raise ValueError("duplicate step IDs")
        queue=list(initial)
        completed=[]; failed=[]; evidence=[]; replans=0; interrupted=False; budget=self.budget_units
        while queue and len(completed)+len(failed) < self.max_steps:
            step=queue.pop(0)
            if step.budget_units > budget:
                interrupted=True
                failed.append(step.step_id)
                break
            budget -= step.budget_units
            try:
                artifact=execute(step)
                passed, ids=verify(step, artifact)
                ids=tuple(dict.fromkeys(str(x) for x in ids if str(x)))
                evidence.extend(ids)
                if passed:
                    completed.append(step.step_id)
                    continue
                failed.append(step.step_id)
                if replan is not None and replans < self.max_replans:
                    additions=tuple(replan(step,None))
                    self._validate_replan(additions, completed, failed, queue)
                    queue.extend(additions)
                    replans += 1
                else:
                    interrupted=True
                    break
            except Exception as exc:
                failed.append(step.step_id)
                if replan is not None and replans < self.max_replans:
                    additions=tuple(replan(step,exc))
                    self._validate_replan(additions, completed, failed, queue)
                    queue.extend(additions)
                    replans += 1
                else:
                    interrupted=True
                    break

        interrupted = interrupted or bool(queue)
        accepted = bool(completed) and not interrupted and not failed
        checkpoint_payload={
            "campaign_id":campaign_id,
            "completed_step_ids":tuple(completed),
            "failed_step_ids":tuple(failed),
            "evidence_ids":tuple(dict.fromkeys(evidence)),
            "remaining_budget":budget,
            "interrupted":interrupted,
        }
        checkpoint=CampaignCheckpoint(
            campaign_id, tuple(completed), tuple(failed),
            tuple(dict.fromkeys(evidence)), budget, interrupted,
            self._digest(checkpoint_payload),
        )
        result_payload={**checkpoint_payload,"replans":replans,"accepted":accepted}
        return LongHorizonCampaignResult(
            campaign_id, tuple(completed), tuple(failed),
            tuple(dict.fromkeys(evidence)), replans, interrupted, accepted,
            checkpoint, self._digest(result_payload),
        )

    @staticmethod
    def _validate_replan(
        additions: Sequence[CampaignStep],
        completed: Sequence[str],
        failed: Sequence[str],
        queue: Sequence[CampaignStep],
    ) -> None:
        existing=set(completed)|set(failed)|{s.step_id for s in queue}
        ids=[s.step_id for s in additions]
        if len(set(ids)) != len(ids) or any(x in existing for x in ids):
            raise ValueError("replan introduced duplicate or already completed step")


__all__=["CampaignStep","CampaignCheckpoint","LongHorizonCampaignResult","LongHorizonCampaignManager"]
