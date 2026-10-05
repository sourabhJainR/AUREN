"""Persistent AUREN supervisor daemon with health, stale-task recovery and self-review."""
from __future__ import annotations
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from .execution_controller import ExecutionController
from .autonomous_company import TrustScore

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
    """Long-running owner of task execution; safe to invoke from a service timer or loop."""
    def __init__(self, root: str | Path, *, stale_after_seconds: int = 900,
                 checkpoint_seconds: int = 300, max_tasks_per_tick: int = 20) -> None:
        if stale_after_seconds < 1 or checkpoint_seconds < 1:
            raise ValueError("daemon intervals must be positive")
        self.controller = ExecutionController(root)
        self.stale_after_seconds = stale_after_seconds
        self.checkpoint_seconds = checkpoint_seconds
        self.max_tasks_per_tick = max_tasks_per_tick
        self.last_checkpoint: HealthCheckpoint | None = None

    def checkpoint(self) -> HealthCheckpoint:
        stale = self.controller.detect_stale(self.stale_after_seconds)
        resumed = 0
        for task in stale:
            self.controller.recover_stale(task.id, reason="stale heartbeat detected")
        scores = self.controller.resume_pending(limit=self.max_tasks_per_tick)
        reviewed = self.controller.self_review(limit=self.max_tasks_per_tick)
        score = min((s.score for s in scores), default=1.0)
        findings = tuple(self.controller.health_findings())
        healthy = not findings and score >= 0.50
        cp = HealthCheckpoint(datetime.now(timezone.utc).isoformat(), len(scores), len(stale),
                              reviewed, score, healthy, findings)
        self.last_checkpoint = cp
        return cp

    def run_forever(self, *, stop: Callable[[], bool] | None = None,
                    sleep_seconds: float | None = None) -> None:
        interval = sleep_seconds if sleep_seconds is not None else self.checkpoint_seconds
        while True:
            if stop and stop():
                return
            self.checkpoint()
            time.sleep(max(0.1, interval))

__all__ = ["PersistentSupervisorDaemon", "HealthCheckpoint"]
