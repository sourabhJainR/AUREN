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


__all__ = ["EvidenceFreshnessPolicy"]
