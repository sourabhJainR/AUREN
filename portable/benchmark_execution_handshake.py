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



def derive_runtime_evidence(*, task_id: str, intent_digest: str, results: dict[str, object],
                             safety_evidence_verified: bool) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """Derive bounded evidence from the existing runtime's executed agents.

    This is attribution only: it never creates verification. Verification is true
    only when the existing verifier agent passed and the runtime safety evidence
    was explicitly verified.
    """
    evidence_ids = tuple(
        f"{intent_digest}:benchmark:{task_id}:{name}"
        for name in sorted(results)
    )
    kinds: list[str] = []
    if evidence_ids:
        kinds.append("canonical execution evidence")
    verifier = results.get("verifier")
    verifier_status = getattr(verifier, "status", None)
    if isinstance(verifier, dict):
        verifier_status = verifier.get("status")
    verified = verifier_status == "passed" and bool(safety_evidence_verified)
    if verified:
        kinds.append("independent verification evidence")
    if evidence_ids and safety_evidence_verified:
        kinds.append("resource and safety evidence")
    # Classification is contract-level, not one-to-one with agent IDs.
    return evidence_ids, tuple(kinds), verified


__all__ = ["BenchmarkExecutionHandshake", "BenchmarkExecutionReceipt", "derive_runtime_evidence"]
