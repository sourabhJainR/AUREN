"""Fail-closed completion handshake for benchmark execution requests."""
from __future__ import annotations
from dataclasses import dataclass
from .benchmark_task_dispatch import BenchmarkExecutionRequest


@dataclass(frozen=True)
class BenchmarkExecutionReceipt:
    task_id: str
    domain: str
    holdout: bool
    evidence_ids: tuple[str, ...]
    success: bool
    verified: bool
    reason: str

    @property
    def accepted(self) -> bool:
        return self.success and self.verified and bool(self.evidence_ids)

    def as_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id, "domain": self.domain, "holdout": self.holdout,
            "evidence_ids": list(self.evidence_ids), "success": self.success,
            "verified": self.verified, "accepted": self.accepted, "reason": self.reason,
        }


class BenchmarkExecutionHandshake:
    def complete(self, request: BenchmarkExecutionRequest, *, evidence_ids=(), success=False, verified=False) -> BenchmarkExecutionReceipt:
        ids = tuple(str(x) for x in evidence_ids if str(x))
        if not ids:
            return BenchmarkExecutionReceipt(request.task_id, request.domain, request.holdout, (), bool(success), bool(verified), "missing evidence")
        if not verified:
            return BenchmarkExecutionReceipt(request.task_id, request.domain, request.holdout, ids, bool(success), False, "independent verification required")
        return BenchmarkExecutionReceipt(
            request.task_id, request.domain, request.holdout, ids, bool(success), True,
            "accepted" if success else "execution failed",
        )


__all__ = ["BenchmarkExecutionHandshake", "BenchmarkExecutionReceipt"]
