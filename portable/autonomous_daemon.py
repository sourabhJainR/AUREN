"Persistent AUREN supervisor daemon with bounded, responsive control loops."
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .execution_controller import ExecutionController


@dataclass(frozen=True)
class HealthCheckpoint:
    at: str
    resumed: int
    stale: int
    reviewed: int
    trust: float
    healthy: bool
    findings: tuple[str, ...]


class PersistentSupervisorDaemon:
    """Long-running task owner with responsive shutdown and bounded failure handling."""

    def __init__(
        self,
        root: str | Path,
        *,
        stale_after_seconds: int = 900,
        checkpoint_seconds: int = 300,
        max_tasks_per_tick: int = 20,
    ) -> None:
        if stale_after_seconds < 1 or checkpoint_seconds < 1:
            raise ValueError("daemon intervals must be positive")
        if max_tasks_per_tick < 1:
            raise ValueError("max_tasks_per_tick must be positive")
        self.controller = ExecutionController(root)
        self.stale_after_seconds = stale_after_seconds
        self.checkpoint_seconds = checkpoint_seconds
        self.max_tasks_per_tick = max_tasks_per_tick
        self.last_checkpoint: HealthCheckpoint | None = None

    def checkpoint(self) -> HealthCheckpoint:
        stale = self.controller.detect_stale(self.stale_after_seconds)
        for task in stale:
            plans = self.controller.generate_alternate_plans(task.id, "stale heartbeat detected")
            chosen = None
            last_reason = "no acceptable alternate plan"
            for plan in plans:
                ok, reason, evidence = self.controller.confirm_alternate(task.id, plan)
                last_reason = reason
                self.controller.company.record(
                    task.work_unit_id, "alternate", "accepted" if ok else "rejected", reason, evidence
                )
                if ok:
                    chosen = plan
                    break
            self.controller.recover_stale(
                task.id, reason=f"stale heartbeat detected; alternate={chosen or 'none'}; {last_reason}"
            )
        scores = self.controller.resume_pending(limit=self.max_tasks_per_tick)
        reviewed = self.controller.self_review(limit=self.max_tasks_per_tick)
        score = min((s.score for s in scores), default=1.0)
        findings = tuple(self.controller.health_findings())
        healthy = not findings and score >= 0.50
        cp = HealthCheckpoint(
            datetime.now(timezone.utc).isoformat(), len(scores), len(stale),
            reviewed, score, healthy, findings,
        )
        self.last_checkpoint = cp
        return cp

    def run_forever(
        self,
        *,
        stop: Callable[[], bool] | None = None,
        sleep_seconds: float | None = None,
        max_cycles: int | None = None,
        max_consecutive_failures: int = 3,
    ) -> None:
        """Run iteratively; poll shutdown at least once per second while idle.

        max_cycles supports bounded service invocations and tests. Transient
        checkpoint failures are retried only up to the configured consecutive
        failure budget; exhaustion raises rather than silently looping forever.
        """
        interval = self.checkpoint_seconds if sleep_seconds is None else float(sleep_seconds)
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("sleep_seconds must be finite and positive")
        if max_cycles is not None and max_cycles < 1:
            raise ValueError("max_cycles must be positive when supplied")
        if max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be positive")

        cycles = 0
        consecutive_failures = 0
        while max_cycles is None or cycles < max_cycles:
            if stop is not None and stop():
                return
            cycles += 1
            try:
                self.checkpoint()
            except Exception as exc:
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    raise RuntimeError(
                        f"supervisor stopped after {consecutive_failures} consecutive checkpoint failures"
                    ) from exc
            else:
                consecutive_failures = 0

            # Short sleep slices keep stop/cancel signals responsive even when
            # the configured checkpoint interval is long. This is an iterative
            # loop, not recursive retry; failure budgets guarantee termination.
            # Reuse the controller's durable quota schedule. If a provider
            # supplied a reset time sooner than the regular health cadence, wake
            # at that deadline; otherwise remain in low-cost local sleep. No model
            # call is made by this timer calculation.
            quota_delay = self.controller.seconds_until_quota_due()
            remaining = min(interval, max(1.0, quota_delay)) if quota_delay is not None else interval
            while remaining > 0:
                if stop is not None and stop():
                    return
                delay = min(1.0, remaining)
                time.sleep(delay)
                remaining -= delay


__all__ = ["PersistentSupervisorDaemon", "HealthCheckpoint"]
