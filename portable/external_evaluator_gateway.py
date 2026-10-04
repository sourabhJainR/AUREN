"""Bounded subprocess gateway for independent sealed Arena evaluators.

The gateway transports opaque campaign requests to an externally supplied
process and imports only its signed outcome receipt. It does not execute the
benchmark, inspect oracle answers, or grant lifecycle authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess
from typing import Sequence

from .sealed_arena_boundary import ExternalOutcomeReceipt, SealedArenaBoundary, SealedArenaEvidence, SealedCampaignRequest
from .sealed_arena_exchange import export_campaign_request, import_outcome_receipt


@dataclass(frozen=True, slots=True)
class ExternalEvaluatorCommand:
    argv: tuple[str, ...]
    timeout_seconds: float = 30.0
    max_output_bytes: int = 1_048_576
    environment: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.argv or any(not str(part).strip() for part in self.argv):
            raise ValueError("external evaluator command is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        if any(not key.strip() for key, _value in self.environment):
            raise ValueError("environment keys must be non-empty")


class ExternalEvaluatorGateway:
    """Exchange a sealed request with an evaluator outside the AUREN process."""

    def __init__(self, boundary: SealedArenaBoundary) -> None:
        self.boundary = boundary

    def evaluate(self, request: SealedCampaignRequest, command: ExternalEvaluatorCommand) -> SealedArenaEvidence:
        completed = subprocess.run(
            list(command.argv),
            input=export_campaign_request(request),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            timeout=command.timeout_seconds,
            check=False,
            env={"PATH": os.environ.get("PATH", ""), **dict(command.environment)},
        )
        if completed.returncode != 0:
            raise RuntimeError(f"external evaluator failed with exit code {completed.returncode}")
        if len(completed.stdout) > command.max_output_bytes:
            raise ValueError("external evaluator receipt exceeds output budget")
        receipt = import_outcome_receipt(completed.stdout)
        return self.boundary.accept(request, receipt)


__all__ = ["ExternalEvaluatorCommand", "ExternalEvaluatorGateway"]
