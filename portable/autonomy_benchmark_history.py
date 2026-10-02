"""Persistent, evidence-backed autonomy benchmark history."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .autonomy_benchmark import DIMENSIONS, AutonomyBenchmark
from .learning_steward import LearningSteward
from runtime.task_memory import approach_history


@dataclass(frozen=True)
class BenchmarkTrend:
    samples: int
    latest: float
    prior_average: float
    delta: float
    regressed: bool

    def as_dict(self) -> dict[str, object]:
        return {"samples": self.samples, "latest": round(self.latest, 3),
                "prior_average": round(self.prior_average, 3), "delta": round(self.delta, 3),
                "regressed": self.regressed}


class AutonomyBenchmarkHistory:
    KEY = "team:autonomy-benchmark"

    def __init__(self, root: Path, *, regression_delta: float = .05) -> None:
        self.root = Path(root)
        self.regression_delta = max(0.0, min(1.0, float(regression_delta)))

    def record(self, benchmark: AutonomyBenchmark, *, task: str, evidence_ids=()) -> None:
        LearningSteward(self.root, run_id="autonomy-benchmark", task=task).record_experience(
            key=self.KEY, outcome="passed" if benchmark.gate_passed else "failed",
            evidence_quality=benchmark.overall, cost_score=1.0 - benchmark.scores["resource_efficiency"],
            duration_seconds=0.0,
            decision=json.dumps({"scores": dict(benchmark.scores), "overall": benchmark.overall}, sort_keys=True),
            evidence_ids=evidence_ids,
        )

    def trend(self, *, limit: int = 40) -> BenchmarkTrend:
        rows=approach_history(self.root,self.KEY,limit=max(1,int(limit)),exact=True)
        values=[]
        for row in rows:
            try:
                detail=json.loads(str(row.get("detail","{}")))
                decision=detail.get("decision",{})
                if isinstance(decision,str): decision=json.loads(decision)
                values.append(float(decision["overall"]))
            except (TypeError,ValueError,KeyError,json.JSONDecodeError):
                continue
        if not values:
            return BenchmarkTrend(0,0.0,0.0,0.0,False)
        # approach_history returns durable records in chronological order.
        # Treat the newest observation as the current state.
        latest=values[-1]
        prior=values[:-1]
        prior_average=sum(prior)/len(prior) if prior else latest
        delta=latest-prior_average
        return BenchmarkTrend(len(values),latest,prior_average,delta,
                              len(prior)>=2 and delta < -self.regression_delta)


__all__=["AutonomyBenchmarkHistory","BenchmarkTrend"]
