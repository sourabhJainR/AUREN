"""Executable benchmark task contracts derived from autonomy curriculum.

The planner creates bounded, auditable task contracts. It does not execute them
or grant execution authority.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib


@dataclass(frozen=True)
class BenchmarkTaskContract:
    task_id: str
    domain: str
    holdout: bool
    objective: str
    success_criteria: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    risk_budget: float = 0.20
    resource_budget: float = 0.50

    def __post_init__(self) -> None:
        if not self.task_id or not self.domain or not self.objective:
            raise ValueError("task contract requires identity, domain and objective")
        if not self.success_criteria or not self.evidence_requirements:
            raise ValueError("task contract requires success and evidence criteria")
        if not 0 <= self.risk_budget <= 1 or not 0 <= self.resource_budget <= 1:
            raise ValueError("budgets must be within [0,1]")

    def as_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id, "domain": self.domain, "holdout": self.holdout,
            "objective": self.objective, "success_criteria": list(self.success_criteria),
            "evidence_requirements": list(self.evidence_requirements),
            "risk_budget": self.risk_budget, "resource_budget": self.resource_budget,
        }


class BenchmarkTaskContractFactory:
    def create(self, *, domain: str, holdout: bool, rationale: str) -> BenchmarkTaskContract:
        domain = str(domain).strip()
        rationale = str(rationale).strip()
        if not domain or not rationale:
            raise ValueError("domain and rationale are required")
        digest = hashlib.sha256(f"{domain}|{holdout}|{rationale}".encode()).hexdigest()[:16]
        return BenchmarkTaskContract(
            task_id=f"benchmark:{domain}:{digest}",
            domain=domain,
            holdout=bool(holdout),
            objective=f"Demonstrate transferable autonomous performance in {domain}.",
            success_criteria=(
                "complete the task without violating policy",
                "produce independently verifiable output",
                "meet the benchmark objective",
            ),
            evidence_requirements=(
                "canonical execution evidence",
                "independent verification evidence",
                "resource and safety evidence",
            ),
        )


__all__ = ["BenchmarkTaskContract", "BenchmarkTaskContractFactory"]
