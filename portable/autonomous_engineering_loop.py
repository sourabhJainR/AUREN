"""Bounded closed-loop engineering coordinator with post-implementation review."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .engineering_evolution import EngineeringEvolutionControlPlane, FailurePrediction, HistoricalDecomposition
from .multi_hat_self_review import SelfReviewReport


@dataclass(frozen=True)
class EngineeringLoopDecision:
    action: str
    reason: str
    failure_prediction: FailurePrediction
    decomposition: HistoricalDecomposition | None = None
    gate_passed: bool = False


@dataclass(frozen=True)
class EngineeringLoopReceipt:
    episode_id: str
    iterations: int
    accepted: bool
    terminal_action: str
    evidence_ids: tuple[str, ...]
    remediation_ids: tuple[str, ...]
    decisions: tuple[EngineeringLoopDecision, ...]


class AutonomousEngineeringLoop:
    """Connect observe -> predict -> plan -> execute -> verify -> self-review -> learn."""

    def __init__(self, control_plane: EngineeringEvolutionControlPlane, *, max_iterations: int = 5) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        self.control_plane, self.max_iterations = control_plane, max_iterations

    def run(
        self,
        *,
        episode_id: str,
        task_family: str,
        capability: str,
        execute: Callable[[HistoricalDecomposition], Any],
        verify: Callable[[Any], tuple[bool, Iterable[str]]],
        learn: Callable[[Any, tuple[str, ...]], None] | None = None,
        promote: Callable[[Any, tuple[str, ...]], bool] | None = None,
        failure_threshold: float = .5,
        self_review: Callable[[Any, tuple[str, ...]], SelfReviewReport] | None = None,
    ) -> EngineeringLoopReceipt:
        if not episode_id.strip() or not task_family.strip() or not capability.strip():
            raise ValueError("episode_id, task_family and capability are required")
        if not 0 <= failure_threshold <= 1:
            raise ValueError("failure_threshold must be between 0 and 1")

        decisions: list[EngineeringLoopDecision] = []
        evidence: list[str] = []
        remediation: list[str] = []
        accepted = False
        terminal = "escalate"
        iteration = 0

        for iteration in range(1, self.max_iterations + 1):
            prediction = self.control_plane.failure_prediction(
                task_family=task_family, capability=capability
            )
            plan = self.control_plane.historical_decomposition(
                task_family=task_family, capability=capability
            )
            if prediction.probability >= failure_threshold:
                decisions.append(EngineeringLoopDecision(
                    "gate",
                    "historical failure risk exceeded pre-execution threshold",
                    prediction, plan, False,
                ))
                break
            if not plan.source_findings:
                decisions.append(EngineeringLoopDecision(
                    "noop", "no persistent remediation work remains", prediction, plan, True
                ))
                accepted = True
                terminal = "complete"
                break

            decisions.append(EngineeringLoopDecision(
                "execute", "plan passed pre-execution risk gate", prediction, plan, True
            ))
            result = execute(plan)
            passed, ids = verify(result)
            current = tuple(dict.fromkeys(str(x) for x in ids if str(x)))
            evidence.extend(current)

            for finding_id in plan.source_findings:
                self.control_plane.backlog.upsert(
                    finding_id=finding_id,
                    task_family=task_family,
                    capability=capability,
                    hat="autonomous-loop",
                    severity="high",
                    title=f"Autonomous episode {episode_id}",
                    detail="Verification result recorded by bounded engineering loop.",
                    recommendation="retain verification evidence and resolve only when independently verified",
                    evidence_ids=current,
                    status="resolved" if passed else "pending",
                    session_id=episode_id,
                    episode_id=episode_id,
                    resolution_evidence_ids=current if passed else (),
                )
                remediation.append(finding_id)

            if not passed:
                if learn:
                    learn(result, current)
                continue

            if self_review is not None:
                report = self_review(result, current)
                if report.has_findings:
                    for finding in report.findings:
                        self.control_plane.backlog.upsert(
                            finding_id=finding.stable_id,
                            task_family=task_family,
                            capability=capability,
                            hat=finding.hat.value,
                            severity=finding.severity,
                            title=finding.title,
                            detail=finding.detail,
                            recommendation=finding.recommendation,
                            evidence_ids=tuple(dict.fromkeys(current + finding.evidence_ids)),
                            status="pending",
                            session_id=episode_id,
                            episode_id=episode_id,
                        )
                        remediation.append(finding.stable_id)
                    if report.developer_decision != "accept":
                        terminal = (
                            "self_review_stop"
                            if report.developer_decision == "stop"
                            else "self_review_address"
                        )
                        return EngineeringLoopReceipt(
                            episode_id, iteration, False, terminal,
                            tuple(dict.fromkeys(evidence)),
                            tuple(dict.fromkeys(remediation)),
                            tuple(decisions),
                        )

            if learn:
                learn(result, current)
            accepted = promote(result, current) if promote else True
            terminal = "promoted" if accepted else "promotion_blocked"
            break

        return EngineeringLoopReceipt(
            episode_id,
            iteration,
            accepted,
            terminal,
            tuple(dict.fromkeys(evidence)),
            tuple(dict.fromkeys(remediation)),
            tuple(decisions),
        )


__all__ = ["EngineeringLoopDecision", "EngineeringLoopReceipt", "AutonomousEngineeringLoop"]
