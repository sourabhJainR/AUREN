"""Deterministic, bounded recovery-path selection for AUREN workers.

This module is deliberately provider-neutral. It chooses a next action; it does
not execute tools, reset budgets, approve side effects, or mark work successful.
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
    """Choose one safe recovery action without hiding uncertainty.

    Strategies are caller-supplied stable IDs ordered by caller preference.
    A retry is allowed only after progress; otherwise the policy selects a
    different, untried strategy. Ambiguous external effects always block until
    reconciled. This function never treats recovery selection as task success.
    """
    if type(attempts_remaining) is not int or attempts_remaining < 0:
        raise ValueError("attempts_remaining must be a non-negative integer")
    if not isinstance(failure_class, str) or not failure_class.strip():
        raise ValueError("failure_class must be non-empty text")
    if side_effect_state not in {"none", "confirmed", "reconciled", "ambiguous"}:
        raise ValueError("unsupported side_effect_state")
    candidates = tuple(candidate_strategies)
    attempted = set(attempted_strategies)
    if any(not isinstance(item, str) or not item.strip() for item in candidates):
        raise ValueError("candidate strategies must be non-empty text")
    if any(not isinstance(item, str) or not item.strip() for item in attempted):
        raise ValueError("attempted strategies must be non-empty text")
    if len(set(candidates)) != len(candidates):
        raise ValueError("candidate strategies must be unique")
    if cancelled:
        return RecoveryDecision("stop", None, "cancellation requested")
    if deadline_expired:
        return RecoveryDecision("stop", None, "execution deadline expired")
    if not checkpoint_verified:
        return RecoveryDecision("block", None, "checkpoint is missing or unverified")
    if side_effect_state == "ambiguous":
        return RecoveryDecision("block", None, "external effect requires reconciliation")
    if attempts_remaining == 0:
        return RecoveryDecision("block", None, "recovery attempt budget exhausted")

    available = next((item for item in candidates if item not in attempted), None)
    if not progress_made:
        if available is None:
            return RecoveryDecision("block", None, "no untried strategy remains after no progress")
        return RecoveryDecision(
            "alternate", available,
            f"no progress after {failure_class.strip()}; switch to an untried strategy",
        )
    if available is not None:
        return RecoveryDecision(
            "alternate", available,
            f"progress stopped after {failure_class.strip()}; try the next untried strategy",
        )
    if side_effect_state in {"none", "confirmed", "reconciled"}:
        return RecoveryDecision("retry", None, "progress was recorded; retry within the remaining budget")
    return RecoveryDecision("block", None, "no safe recovery path remains")
