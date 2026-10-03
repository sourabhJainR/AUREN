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
    evidence_kinds: tuple[str, ...]
    required_evidence: tuple[str, ...]
    success: bool
    verified: bool
    reason: str

    @property
    def accepted(self) -> bool:
        return (
            self.success
            and self.verified
            and bool(self.evidence_ids)
            and set(self.required_evidence).issubset(set(self.evidence_kinds))
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "domain": self.domain,
            "holdout": self.holdout,
            "evidence_ids": list(self.evidence_ids),
            "evidence_kinds": list(self.evidence_kinds),
            "required_evidence": list(self.required_evidence),
            "success": self.success,
            "verified": self.verified,
            "accepted": self.accepted,
            "reason": self.reason,
        }


class BenchmarkExecutionHandshake:
    def complete(
        self,
        request: BenchmarkExecutionRequest,
        *,
        evidence_ids=(),
        evidence_kinds=(),
        success=False,
        verified=False,
    ) -> BenchmarkExecutionReceipt:
        ids = tuple(str(x) for x in evidence_ids if str(x))
        kinds = tuple(str(x).strip() for x in evidence_kinds if str(x).strip())
        required = tuple(str(x).strip() for x in request.required_evidence if str(x).strip())
        base = (request.task_id, request.domain, request.holdout, ids, kinds, required, bool(success), bool(verified))
        if not ids:
            return BenchmarkExecutionReceipt(*base, "missing evidence")
        if len(kinds) != len(ids):
            return BenchmarkExecutionReceipt(*base, "evidence classification is incomplete")
        if not verified:
            return BenchmarkExecutionReceipt(*base, "independent verification required")
        missing = tuple(sorted(set(required).difference(kinds)))
        if missing:
            return BenchmarkExecutionReceipt(*base, "required evidence incomplete: " + ", ".join(missing))
        return BenchmarkExecutionReceipt(
            *base, "accepted" if success else "execution failed"
        )


__all__ = ["BenchmarkExecutionHandshake", "BenchmarkExecutionReceipt"]
