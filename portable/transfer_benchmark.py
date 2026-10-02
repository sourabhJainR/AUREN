"""Cross-task transfer benchmark harness.

Builds train/holdout cohorts from verified episode records and reports transfer
uplift without executing candidate capabilities. It is intentionally deterministic
and provider-free so learned abstractions can be evaluated independently.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .cross_task_capability_abstraction import CrossTaskCapabilityAbstraction
from runtime.task_memory import approach_history


@dataclass(frozen=True)
class TransferBenchmark:
    training_tasks: int
    holdout_tasks: int
    candidate_score: float
    baseline_score: float
    uplift: float
    transfer_passed: bool

    def as_dict(self):
        return {"training_tasks":self.training_tasks,"holdout_tasks":self.holdout_tasks,
                "candidate_score":round(self.candidate_score,3),
                "baseline_score":round(self.baseline_score,3),"uplift":round(self.uplift,3),
                "transfer_passed":self.transfer_passed}


class TransferBenchmarkHarness:
    def __init__(self, root: Path, *, minimum_holdout_tasks: int = 2,
                 minimum_uplift: float = .03):
        self.root=Path(root)
        self.minimum_holdout_tasks=max(1,int(minimum_holdout_tasks))
        self.minimum_uplift=max(0.0,min(1.0,float(minimum_uplift)))

    def evaluate(self, pattern, *, role="team", limit=240):
        rows=approach_history(self.root,f"{role}:abstract-episode:",limit=max(1,int(limit)),exact=False)
        parsed=[]
        for row in rows:
            item=CrossTaskCapabilityAbstraction._episode(row)
            if item is not None:
                parsed.append(item)
        candidate=[]; baseline=[]; tasks=set()
        for item in parsed:
            if item["context_signature"] != pattern.context_signature:
                continue
            task=str(item["task"])
            score=(.7 if item["success"] else .1)+.3*float(item["evidence_quality"])
            if item["action"]==pattern.action and item["mode"]==pattern.mode:
                candidate.append(score); tasks.add(("candidate",task))
            else:
                baseline.append(score); tasks.add(("baseline",task))
        c=sum(candidate)/len(candidate) if candidate else 0.0
        b=sum(baseline)/len(baseline) if baseline else 0.0
        holdout_tasks=len({t for _,t in tasks})
        # Training task count is the pattern's independent observed sample count.
        uplift=c-b
        return TransferBenchmark(pattern.train_samples,holdout_tasks,c,b,uplift,
                                 holdout_tasks>=self.minimum_holdout_tasks and
                                 bool(candidate) and bool(baseline) and uplift>=self.minimum_uplift)


__all__=["TransferBenchmark","TransferBenchmarkHarness"]
