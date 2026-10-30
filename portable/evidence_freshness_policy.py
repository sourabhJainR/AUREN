"""Freshness policy for evidence that can steer execution decisions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class EvidenceFreshnessPolicy:
    max_age_seconds: float = 30 * 24 * 3600

    def __post_init__(self) -> None:
        if self.max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")

    def weight(self, observed_at: str | None, *, now: datetime | None = None) -> float:
        if not observed_at:
            # Legacy evidence without timestamps remains usable but cannot claim
            # freshness; explicit external ingestion writes timestamps.
            return 1.0
        try:
            stamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        age = max(0.0, (current - stamp).total_seconds())
        if age > self.max_age_seconds:
            return 0.0
        return max(0.0, 1.0 - age / self.max_age_seconds)

    def external_weight(
        self,
        *,
        published_at: str | None = None,
        observed_at: str | None = None,
        available_at: str | None = None,
        retrieved_at: str | None = None,
        now: datetime | None = None,
    ) -> float:
        """Score external evidence without confusing retrieval with source freshness.

        The freshness anchor is the earliest trustworthy availability timestamp
        supplied by the source contract: available_at, then published_at, then
        source observed_at. retrieved_at is retained for provenance only and is
        never a freshness anchor. Unknown, malformed and future timestamps fail
        closed with zero weight. The legacy weight() method remains unchanged.
        """
        del retrieved_at  # Retrieval proves when we fetched it, not when it became available.
        anchor = next(
            (value for value in (available_at, published_at, observed_at)
             if isinstance(value, str) and value.strip()),
            None,
        )
        if anchor is None:
            return 0.0
        try:
            stamp = datetime.fromisoformat(anchor.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if stamp > current:
            return 0.0
        return self.weight(anchor, now=current)


__all__ = ["EvidenceFreshnessPolicy"]
