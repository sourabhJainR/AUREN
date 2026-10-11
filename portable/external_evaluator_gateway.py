"""Bounded subprocess gateway for independent sealed Arena evaluators.

The gateway transports opaque campaign requests to an externally supplied
process and imports only its signed outcome receipt. It does not execute the
benchmark, inspect oracle answers, or grant lifecycle authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
import subprocess
import threading

from .sealed_arena_boundary import SealedArenaBoundary, SealedArenaEvidence, SealedCampaignRequest
from .sealed_arena_exchange import export_campaign_request, import_outcome_receipt
from .sealed_arena_replay import SealedArenaReplayLedger


@dataclass(frozen=True, slots=True)
class ExternalEvaluatorCommand:
    argv: tuple[str, ...]
    timeout_seconds: float = 30.0
    max_output_bytes: int = 1_048_576
    environment: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.argv, tuple) or not self.argv or any(
            not isinstance(part, str) or not part.strip() for part in self.argv
        ):
            raise ValueError("external evaluator argv must be a non-empty tuple of non-empty strings")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a finite positive number")
        if isinstance(self.max_output_bytes, bool) or not isinstance(self.max_output_bytes, int) or self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be a positive integer")
        if not isinstance(self.environment, tuple):
            raise ValueError("environment must be an immutable tuple of key/value pairs")
        keys = []
        for item in self.environment:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("environment entries must be key/value tuples")
            key, value = item
            if not isinstance(key, str) or not key.strip() or not isinstance(value, str):
                raise ValueError("environment keys must be non-empty strings and values must be strings")
            if key.upper() in {"PATH", "PYTHONPATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"}:
                raise ValueError(f"environment override is not allowed for {key}")
            keys.append(key)
        if len(keys) != len(set(keys)):
            raise ValueError("environment keys must be unique")


class ExternalEvaluatorGateway:
    """Exchange a sealed request with an evaluator outside the AUREN process."""

    def __init__(self, boundary: SealedArenaBoundary, replay_ledger: SealedArenaReplayLedger) -> None:
        if not isinstance(replay_ledger, SealedArenaReplayLedger):
            raise TypeError("a persistent SealedArenaReplayLedger is required")
        self.boundary = boundary
        self.replay_ledger = replay_ledger

    def evaluate(self, request: SealedCampaignRequest, command: ExternalEvaluatorCommand) -> SealedArenaEvidence:
        if not request.run_nonce.strip():
            raise ValueError("sealed campaign request requires a unique run_nonce")
        self.replay_ledger.claim(
            run_nonce=request.run_nonce,
            campaign_digest=request.campaign_digest,
            corpus_digest=request.corpus_digest,
        )
        process = subprocess.Popen(
            list(command.argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            env={"PATH": os.environ.get("PATH", ""), **dict(command.environment)},
        )
        assert process.stdin is not None
        assert process.stdout is not None

        # Enforce the output budget while bytes are being read. Checking the size
        # after subprocess.run(capture_output=True) is too late: an evaluator can
        # exhaust the parent process's memory before the post-hoc check executes.
        output = bytearray()
        output_limit_exceeded = threading.Event()

        def drain_output() -> None:
            try:
                while True:
                    remaining = command.max_output_bytes + 1 - len(output)
                    if remaining <= 0:
                        output_limit_exceeded.set()
                        process.kill()
                        return
                    chunk = process.stdout.read1(min(65_536, remaining))
                    if not chunk:
                        return
                    output.extend(chunk)
                    if len(output) > command.max_output_bytes:
                        output_limit_exceeded.set()
                        process.kill()
                        return
            except (OSError, ValueError):
                # Closing a pipe after a timeout/kill is expected.
                return

        def send_request() -> None:
            try:
                process.stdin.write(export_campaign_request(request))
                process.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                # The evaluator may exit early; its exit status is checked below.
                return

        reader = threading.Thread(target=drain_output, name="arena-evaluator-stdout", daemon=True)
        writer = threading.Thread(target=send_request, name="arena-evaluator-stdin", daemon=True)
        reader.start()
        writer.start()
        timed_out = False
        try:
            process.wait(timeout=command.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()

        writer.join(timeout=1.0)
        reader.join(timeout=1.0)
        if output_limit_exceeded.is_set():
            raise ValueError("external evaluator receipt exceeds output budget")
        if timed_out:
            raise RuntimeError("external evaluator timed out")
        if reader.is_alive():
            raise RuntimeError("external evaluator output stream did not close within the bounded wait")
        if process.returncode != 0:
            raise RuntimeError(f"external evaluator failed with exit code {process.returncode}")
        receipt = import_outcome_receipt(bytes(output))
        return self.boundary.accept(request, receipt)


__all__ = ["ExternalEvaluatorCommand", "ExternalEvaluatorGateway"]
