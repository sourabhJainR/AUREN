#!/usr/bin/env python3
"""Canonical provider-neutral bounded feedback loop for AUREN.

This module adapts the strongest implementation concepts from Forward Future's
Loopy project: fresh observation, one bounded action, explicit verification,
evidence recording, finite execution boundaries, approval stops, compact
receipts, and explicit terminal states. It does not import or depend on Loopy.

When a :class:`ProvenanceLedger` is supplied, every loop start, bounded pass,
and terminal outcome is appended to the same append-only execution chain used
by the wider AUREN lifecycle. The loop never grants permissions or performs
external side effects on its own.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Sequence

if TYPE_CHECKING:
    from .agency_provenance import ProvenanceLedger

LOOP_TERMINAL_STATES = (
    "success", "clean_no_op", "blocked", "approval_required",
    "exhausted", "no_progress", "error",
)


@dataclass(frozen=True)
class LoopDefinition:
    """Immutable description of one bounded engineering loop."""
    name: str
    objective: str
    acceptance_check: str
    scope: str
    pass_limit: int
    stop_on_no_progress: bool = True
    approval_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.objective.strip():
            raise ValueError("loop name and objective are required")
        if not self.acceptance_check.strip():
            raise ValueError("acceptance_check is required")
        if not self.scope.strip():
            raise ValueError("scope is required")
        if self.pass_limit < 1:
            raise ValueError("pass_limit must be at least 1")

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "objective": self.objective,
            "acceptance_check": self.acceptance_check,
            "scope": self.scope,
            "pass_limit": self.pass_limit,
            "stop_on_no_progress": self.stop_on_no_progress,
            "approval_actions": list(self.approval_actions),
        }

    def digest(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LoopAction:
    """Host-selected action for one pass."""
    description: str
    evidence: tuple[str, ...] = ()
    requires_approval: bool = False


@dataclass(frozen=True)
class VerificationResult:
    """Verification result returned by the host after an action."""
    passed: bool
    complete: bool = False
    progress: bool = False
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoopPass:
    number: int
    observation: str
    action: str
    evidence: tuple[str, ...]
    verified: bool
    progress: bool
    complete: bool = False
    next_action: str = ""
    approval_required: bool = False
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "observation": self.observation,
            "action": self.action,
            "evidence": list(self.evidence),
            "verified": self.verified,
            "progress": self.progress,
            "complete": self.complete,
            "next_action": self.next_action,
            "approval_required": self.approval_required,
            "error": self.error,
        }


@dataclass(frozen=True)
class LoopRunReceipt:
    """Compact, replayable result of one bounded loop execution."""
    definition_digest: str
    loop_name: str
    scope: str
    acceptance_check: str
    boundary: str
    result: str
    passes: tuple[LoopPass, ...] = field(default_factory=tuple)
    next_step: str = ""
    context_evidence_digest: str = ""
    provenance_record_hash: str = ""
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        if self.result not in LOOP_TERMINAL_STATES:
            raise ValueError(f"unsupported loop result: {self.result}")
        if not self.definition_digest:
            raise ValueError("definition_digest is required")
        if not self.boundary.strip():
            raise ValueError("boundary is required")
        if not self.receipt_digest:
            payload = json.dumps(self._unsigned_dict(), sort_keys=True, separators=(",", ":"))
            object.__setattr__(self, "receipt_digest", hashlib.sha256(payload.encode("utf-8")).hexdigest())

    def _unsigned_dict(self) -> dict[str, object]:
        return {
            "definition_digest": self.definition_digest,
            "loop_name": self.loop_name,
            "scope": self.scope,
            "acceptance_check": self.acceptance_check,
            "boundary": self.boundary,
            "result": self.result,
            "passes": [item.as_dict() for item in self.passes],
            "next_step": self.next_step,
            "context_evidence_digest": self.context_evidence_digest,
        }

    def as_dict(self) -> dict[str, object]:
        value = self._unsigned_dict()
        value["provenance_record_hash"] = self.provenance_record_hash
        value["receipt_digest"] = self.receipt_digest
        return value


Observe = Callable[[int], str]
Choose = Callable[[str, int], LoopAction | None]
Act = Callable[[LoopAction, int], None]
Verify = Callable[[LoopAction, int], VerificationResult | bool | tuple[bool, Sequence[str]]]


class BoundedLoop:
    """Run a loop with explicit boundaries and fail-closed terminal states."""

    def __init__(self, definition: LoopDefinition) -> None:
        self.definition = definition

    @staticmethod
    def _normalize_verification(
        value: VerificationResult | bool | tuple[bool, Sequence[str]],
    ) -> VerificationResult:
        if isinstance(value, VerificationResult):
            return value
        if isinstance(value, tuple):
            passed, evidence = value
            return VerificationResult(
                passed=bool(passed),
                progress=bool(passed),
                evidence=tuple(str(item) for item in evidence),
            )
        return VerificationResult(passed=bool(value), progress=bool(value))

    def _receipt(
        self,
        *,
        result: str,
        passes: list[LoopPass],
        next_step: str,
        context_evidence_digest: str,
        provenance_record_hash: str = "",
    ) -> LoopRunReceipt:
        return LoopRunReceipt(
            definition_digest=self.definition.digest(),
            loop_name=self.definition.name,
            scope=self.definition.scope,
            acceptance_check=self.definition.acceptance_check,
            boundary=f"max_passes={self.definition.pass_limit}",
            result=result,
            passes=tuple(passes),
            next_step=next_step,
            context_evidence_digest=context_evidence_digest,
            provenance_record_hash=provenance_record_hash,
        )

    def _finish(
        self,
        *,
        result: str,
        passes: list[LoopPass],
        next_step: str,
        provenance: ProvenanceLedger | None,
        run_id: str,
        context_evidence_digest: str,
        artifact_ids: Sequence[str],
    ) -> LoopRunReceipt:
        receipt = self._receipt(
            result=result,
            passes=passes,
            next_step=next_step,
            context_evidence_digest=context_evidence_digest,
        )
        if provenance is None:
            return receipt
        record = provenance.append_evidence_event(
            run_id=run_id,
            event="loop.completed",
            context_evidence_digest=context_evidence_digest,
            detail={
                "loop_name": self.definition.name,
                "definition_digest": self.definition.digest(),
                "receipt_digest": receipt.receipt_digest,
                "result": result,
                "pass_count": len(passes),
                "boundary": f"max_passes={self.definition.pass_limit}",
            },
            artifact_ids=artifact_ids,
        )
        return self._receipt(
            result=result,
            passes=passes,
            next_step=next_step,
            context_evidence_digest=context_evidence_digest,
            provenance_record_hash=record.record_hash,
        )

    def run(
        self,
        *,
        observe: Observe,
        choose: Choose,
        act: Act,
        verify: Verify,
        provenance: ProvenanceLedger | None = None,
        run_id: str | None = None,
        context_evidence_digest: str = "",
        artifact_ids: Sequence[str] = (),
    ) -> LoopRunReceipt:
        """Execute bounded passes and append the complete run to AUREN provenance."""
        if provenance is not None and not str(run_id or "").strip():
            raise ValueError("run_id is required when provenance is supplied")
        effective_run_id = str(run_id or self.definition.name)
        passes: list[LoopPass] = []
        definition_digest = self.definition.digest()

        if provenance is not None:
            provenance.append_evidence_event(
                run_id=effective_run_id,
                event="loop.started",
                context_evidence_digest=context_evidence_digest,
                detail={
                    "loop_name": self.definition.name,
                    "definition_digest": definition_digest,
                    "objective": self.definition.objective,
                    "acceptance_check": self.definition.acceptance_check,
                    "scope": self.definition.scope,
                    "boundary": f"max_passes={self.definition.pass_limit}",
                },
                artifact_ids=artifact_ids,
            )

        for number in range(1, self.definition.pass_limit + 1):
            try:
                observation = str(observe(number))
                action = choose(observation, number)
                if action is None:
                    return self._finish(
                        result="clean_no_op", passes=passes,
                        next_step="nothing; the scoped work is already complete or no safe action is available",
                        provenance=provenance, run_id=effective_run_id,
                        context_evidence_digest=context_evidence_digest, artifact_ids=artifact_ids,
                    )

                if action.requires_approval or action.description in self.definition.approval_actions:
                    passes.append(LoopPass(number, observation, action.description, action.evidence, False, False, approval_required=True))
                    if provenance is not None:
                        provenance.append_evidence_event(
                            run_id=effective_run_id,
                            event="loop.pass.approval_required",
                            context_evidence_digest=context_evidence_digest,
                            detail={
                                "loop_name": self.definition.name,
                                "definition_digest": definition_digest,
                                "pass": number,
                                "observation": observation,
                                "action": action.description,
                                "approval_required": True,
                            },
                            artifact_ids=artifact_ids,
                        )
                    return self._finish(
                        result="approval_required", passes=passes,
                        next_step=f"approve the exact action before continuing: {action.description}",
                        provenance=provenance, run_id=effective_run_id,
                        context_evidence_digest=context_evidence_digest, artifact_ids=artifact_ids,
                    )

                act(action, number)
                verification = self._normalize_verification(verify(action, number))
                pass_record = LoopPass(
                    number=number,
                    observation=observation,
                    action=action.description,
                    evidence=verification.evidence or action.evidence,
                    verified=verification.passed,
                    progress=verification.progress,
                    complete=verification.complete,
                )
                passes.append(pass_record)

                if provenance is not None:
                    provenance.append_evidence_event(
                        run_id=effective_run_id,
                        event="loop.pass.completed",
                        context_evidence_digest=context_evidence_digest,
                        detail={
                            "loop_name": self.definition.name,
                            "definition_digest": definition_digest,
                            "pass": number,
                            "observation": observation,
                            "action": action.description,
                            "verified": verification.passed,
                            "progress": verification.progress,
                            "complete": verification.complete,
                            "evidence": list(pass_record.evidence),
                        },
                        artifact_ids=artifact_ids,
                    )

                if not verification.passed:
                    return self._finish(
                        result="blocked", passes=passes,
                        next_step="verification failed; inspect the evidence before another pass",
                        provenance=provenance, run_id=effective_run_id,
                        context_evidence_digest=context_evidence_digest, artifact_ids=artifact_ids,
                    )
                if verification.complete:
                    return self._finish(
                        result="success", passes=passes,
                        next_step="acceptance criteria met",
                        provenance=provenance, run_id=effective_run_id,
                        context_evidence_digest=context_evidence_digest, artifact_ids=artifact_ids,
                    )
                if not verification.progress and self.definition.stop_on_no_progress:
                    return self._finish(
                        result="no_progress", passes=passes,
                        next_step="no measurable progress after the latest verified action",
                        provenance=provenance, run_id=effective_run_id,
                        context_evidence_digest=context_evidence_digest, artifact_ids=artifact_ids,
                    )
            except Exception as exc:
                passes.append(LoopPass(
                    number=number, observation="", action="", evidence=(),
                    verified=False, progress=False, error=f"{type(exc).__name__}: {exc}",
                ))
                if provenance is not None:
                    provenance.append_evidence_event(
                        run_id=effective_run_id,
                        event="loop.pass.error",
                        context_evidence_digest=context_evidence_digest,
                        detail={
                            "loop_name": self.definition.name,
                            "definition_digest": definition_digest,
                            "pass": number,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                        artifact_ids=artifact_ids,
                    )
                return self._finish(
                    result="error", passes=passes,
                    next_step="inspect the execution error and resume only after the blocker is understood",
                    provenance=provenance, run_id=effective_run_id,
                    context_evidence_digest=context_evidence_digest, artifact_ids=artifact_ids,
                )

        return self._finish(
            result="exhausted", passes=passes,
            next_step="run boundary exhausted; review remaining work before another explicit run",
            provenance=provenance, run_id=effective_run_id,
            context_evidence_digest=context_evidence_digest, artifact_ids=artifact_ids,
        )


@dataclass(frozen=True)
class FeedbackPolicy:
    min_observations: int = 5
    min_success_rate: float = 0.80
    min_verification_rate: float = 0.90
    min_improvement: float = 0.03


class FeedbackLoop:
    """Compatibility learning store; bounded execution stays in BoundedLoop."""

    def __init__(self, root: Path, policy: FeedbackPolicy | None = None) -> None:
        self.root = root.expanduser().resolve()
        self.policy = policy or FeedbackPolicy()
        self.path = self.root / ".ai-harness" / "learning" / "feedback-events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def observe(self, *, task_id: str, outcome: str, verified: bool, strategy: str, evidence: list[str] | None = None) -> dict:
        event = {
            "ts": time.time(), "task_id": task_id, "outcome": outcome,
            "verified": bool(verified), "strategy": strategy, "evidence": list(evidence or []),
        }
        payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
        event["event_digest"] = hashlib.sha256(payload.encode()).hexdigest()[:16]
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def evaluate(self, *, strategy: str) -> dict:
        events: list[dict] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines()[-500:]:
                try:
                    value = json.loads(line)
                    if value.get("strategy") == strategy:
                        events.append(value)
                except json.JSONDecodeError:
                    continue
        n = len(events)
        successes = sum(1 for e in events if e.get("outcome") in {"success", "passed"})
        verified = sum(1 for e in events if e.get("verified"))
        success_rate = successes / n if n else 0.0
        verification_rate = verified / n if n else 0.0
        eligible = n >= self.policy.min_observations and success_rate >= self.policy.min_success_rate and verification_rate >= self.policy.min_verification_rate
        return {
            "strategy": strategy, "observations": n,
            "success_rate": round(success_rate, 4), "verification_rate": round(verification_rate, 4),
            "candidate_eligible": eligible,
            "activation": "blocked_until_regression_and_safety_gates" if eligible else "insufficient_evidence",
        }

    def candidate(self, *, strategy: str, proposed_change: str) -> dict:
        evaluation = self.evaluate(strategy=strategy)
        return {
            "candidate_id": hashlib.sha256(f"{strategy}\0{proposed_change}".encode()).hexdigest()[:16],
            "strategy": strategy, "proposed_change": proposed_change,
            "evaluation": evaluation, "active": False,
            "requires": ["deterministic_regression", "safety_gate", "shadow", "canary", "monitoring"],
        }
