"""Benchmark task dispatch contracts.

Transforms an approved benchmark task contract into an execution request for
the existing runtime. This module does not execute tasks or bypass safety,
strategy, mode, or canary gates.
"""
from __future__ import annotations
from dataclasses import dataclass
from .benchmark_task_contract import BenchmarkTaskContract


@dataclass(frozen=True)
class BenchmarkExecutionRequest:
    task_id: str
    domain: str
    holdout: bool
    objective: str
    risk_budget: float
    resource_budget: float
    required_evidence: tuple[str, ...]
    authority: str = "existing-runtime-only"

    def as_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id, "domain": self.domain, "holdout": self.holdout,
            "objective": self.objective, "risk_budget": self.risk_budget,
            "resource_budget": self.resource_budget,
            "required_evidence": list(self.required_evidence),
            "authority": self.authority,
        }


class BenchmarkTaskDispatcher:
    def dispatch_request(self, contract: BenchmarkTaskContract) -> BenchmarkExecutionRequest:
        if contract.risk_budget > 0.5 or contract.resource_budget > 1.0:
            raise ValueError("benchmark contract exceeds dispatcher bounds")
        return BenchmarkExecutionRequest(
            task_id=contract.task_id,
            domain=contract.domain,
            holdout=contract.holdout,
            objective=contract.objective,
            risk_budget=contract.risk_budget,
            resource_budget=contract.resource_budget,
            required_evidence=contract.evidence_requirements,
        )


__all__ = ["BenchmarkExecutionRequest", "BenchmarkTaskDispatcher"]
