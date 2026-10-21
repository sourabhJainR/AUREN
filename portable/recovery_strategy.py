"""Deterministic, bounded recovery-path selection for AUREN workers.

Provider-neutral policy only: no execution, budget reset, approval, or success claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: str
    strategy: str | None
    reason: str


def choose_recovery_path(
    *,
    candidate_strategies: Iterable[str],
    attempted_strategies: Iterable[str],
    failure_class: str,
    attempts_remaining: int,
    progress_made: bool,
    cancelled: bool = False,
    deadline_expired: bool = False,
    checkpoint_verified: bool = True,
    side_effect_state: str = "none",
) -> RecoveryDecision:
    """Choose one safe action. Strategy IDs are ordered by caller preference."""
    if type(attempts_remaining) is not int or attempts_remaining < 0:
        raise ValueError("attempts_remaining must be a non-negative integer")
    if not isinstance(failure_class, str) or not failure_class.strip():
        raise ValueError("failure_class must be non-empty text")
    if side_effect_state not in {"none", "confirmed", "reconciled", "ambiguous"}:
        raise ValueError("unsupported side_effect_state")

    candidates = tuple(candidate_strategies)
    attempted_items = tuple(attempted_strategies)
    if any(not isinstance(item, str) or not item.strip() for item in candidates + attempted_items):
        raise ValueError("strategy IDs must be non-empty text")
    if len(set(candidates)) != len(candidates):
        raise ValueError("candidate strategies must be unique")
    attempted = set(attempted_items)

    if cancelled:
        return RecoveryDecision("stop", None, "cancellation requested")
    if deadline_expired:
        return RecoveryDecision("stop", None, "execution deadline expired")
    if not checkpoint_verified:
        return RecoveryDecision("block", None, "checkpoint is missing or unverified")
    if side_effect_state == "ambiguous":
        return RecoveryDecision("block", None, "external effect requires reconciliation")
    if side_effect_state == "confirmed":
        return RecoveryDecision("block", None, "confirmed external effect requires task-state reconciliation")
    if attempts_remaining == 0:
        return RecoveryDecision("block", None, "recovery attempt budget exhausted")

    available = next((item for item in candidates if item not in attempted), None)
    if available is not None:
        reason = "no progress" if not progress_made else "progress stopped"
        return RecoveryDecision("alternate", available, f"{reason} after {failure_class.strip()}; switch strategy")
    if progress_made and side_effect_state in {"none", "reconciled"}:
        return RecoveryDecision("retry", None, "progress was recorded; retry within the remaining budget")
    return RecoveryDecision("block", None, "no untried strategy remains after no progress")
