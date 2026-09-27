"""Repository-grounded model-to-patch verification cycle."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .sandboxed_repository import CommandEvidence, CommandSpec, RepositoryExecutionResult, SandboxedRepository


@dataclass(frozen=True)
class PatchProposal:
    intent: str
    files: Mapping[str, str]
    rationale: str = ""
    model: str = "unknown"


@dataclass(frozen=True)
class RepositoryEngineeringCycleResult:
    proposal: PatchProposal
    execution: RepositoryExecutionResult
    accepted: bool
    evidence_ids: tuple[str, ...]
    rejection_reason: str = ""


class RepositoryEngineeringCycle:
    """Run a model proposal through an isolated repository and real commands.

    The proposal source is untrusted input. It can describe file contents but
    cannot choose the source workspace, executable policy, or promotion path.
    """

    def __init__(
        self,
        source: Path | str,
        *,
        allowed_commands: Sequence[str] = ("python", "pytest", "git"),
    ) -> None:
        self.sandbox = SandboxedRepository(source, allowed_commands=allowed_commands)

    def run(
        self,
        proposal: PatchProposal,
        *,
        commands: Sequence[CommandSpec],
        minimum_evidence: int = 1,
    ) -> RepositoryEngineeringCycleResult:
        if not isinstance(proposal, PatchProposal):
            raise TypeError("proposal must be a PatchProposal")
        if not proposal.intent.strip():
            raise ValueError("proposal intent is required")
        if not proposal.files:
            raise ValueError("proposal must contain file changes")
        if minimum_evidence < 1:
            raise ValueError("minimum_evidence must be positive")
        execution = self.sandbox.execute_patch(dict(proposal.files), commands)
        evidence = tuple(dict.fromkeys(execution.evidence_ids))
        accepted = execution.passed and len(evidence) >= minimum_evidence
        reason = "" if accepted else (
            execution.failure or "verification evidence was insufficient"
        )
        return RepositoryEngineeringCycleResult(
            proposal, execution, accepted, evidence, reason
        )

    def propose_and_run(
        self,
        intent: str,
        proposer: Callable[[str], PatchProposal],
        *,
        commands: Sequence[CommandSpec],
        minimum_evidence: int = 1,
    ) -> RepositoryEngineeringCycleResult:
        if not callable(proposer):
            raise TypeError("proposer must be callable")
        proposal = proposer(intent)
        return self.run(proposal, commands=commands, minimum_evidence=minimum_evidence)


__all__ = [
    "PatchProposal",
    "RepositoryEngineeringCycle",
    "RepositoryEngineeringCycleResult",
]
