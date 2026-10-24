"""Bounded subprocess gateway for independent sealed Arena evaluators.

The gateway transports opaque campaign requests to an externally supplied
process and imports only its signed outcome receipt. It does not execute the
benchmark, inspect oracle answers, or grant lifecycle authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import signal
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
            not isinstance(part, str) or not part.strip() or chr(0) in part for part in self.argv
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
        forbidden_environment_keys = {
            "PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT",
            "PYTHONUSERBASE", "PYTHONEXECUTABLE", "PYTHONPLATLIBDIR",
            "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "LD_DEBUG",
            "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH",
            "BASH_ENV", "ENV", "IFS", "GCONV_PATH", "PERL5OPT", "RUBYOPT",
            "NODE_OPTIONS", "JAVA_TOOL_OPTIONS",
        }
        keys = []
        for item in self.environment:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("environment entries must be key/value tuples")
            key, value = item
            if not isinstance(key, str) or not key.strip() or not isinstance(value, str):
                raise ValueError("environment keys must be non-empty strings and values must be strings")
            normalized_key = key.upper()
            if normalized_key in forbidden_environment_keys:
                raise ValueError(f"environment override is not allowed for {key}")
            keys.append(normalized_key)
        if len(keys) != len(set(keys)):
            raise ValueError("environment keys must be unique ignoring case")


def _close_evaluator_streams(process: subprocess.Popen[bytes]) -> None:
    """Close parent-owned evaluator pipes after I/O threads have joined."""
    for stream in (process.stdin, process.stdout):
        if stream is None:
            continue
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _kill_evaluator_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill the evaluator and descendants in its dedicated process group."""
    isolated_terminator = getattr(process, "terminate_isolated", None)
    if callable(isolated_terminator):
        isolated_terminator()
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass


class ExternalEvaluatorGateway:
    """Exchange a sealed request with an evaluator outside the AUREN process."""

    def __init__(
        self,
        boundary: SealedArenaBoundary,
        replay_ledger: SealedArenaReplayLedger,
        *,
        allow_test_verifier: bool = False,
        isolated_executor=None,
    ) -> None:
        if not isinstance(boundary, SealedArenaBoundary):
            raise TypeError("a SealedArenaBoundary is required")
        if not isinstance(replay_ledger, SealedArenaReplayLedger):
            raise TypeError("a persistent SealedArenaReplayLedger is required")
        if type(allow_test_verifier) is not bool:
            raise TypeError("allow_test_verifier must be a boolean")
        if not boundary.has_trusted_custody_verifier and not allow_test_verifier:
            raise ValueError(
                "external evaluator gateway requires a trusted Ed25519 keyring boundary that verifies signed holdout custody; "
                "test-only callbacks require allow_test_verifier=True"
            )
        if boundary._replay_ledger is not None and (
            Path(boundary._replay_ledger.database_path).resolve()
            != Path(replay_ledger.database_path).resolve()
        ):
            raise ValueError("gateway and boundary must share the same replay ledger database")
        if isolated_executor is not None and getattr(isolated_executor, "production_ready", False) is not True:
            raise ValueError("isolated_executor must be an explicitly production-ready OS isolation adapter")
        if not allow_test_verifier and isolated_executor is None:
            raise ValueError(
                "production external evaluator requires a production-ready OS isolation adapter; "
                "the host subprocess fallback is not an isolation boundary"
            )
        self.boundary = boundary
        self.replay_ledger = replay_ledger
        self.isolated_executor = isolated_executor

    def evaluate(self, request: SealedCampaignRequest, command: ExternalEvaluatorCommand) -> SealedArenaEvidence:
        if not request.run_nonce.strip():
            raise ValueError("sealed campaign request requires a unique run_nonce")
        self.replay_ledger.claim(
            run_nonce=request.run_nonce,
            campaign_digest=request.campaign_digest,
            corpus_digest=request.corpus_digest,
        )
        environment = {"PATH": os.environ.get("PATH", ""), **dict(command.environment)}
        payload = export_campaign_request(request)
        input_limit = getattr(getattr(self.isolated_executor, "policy", None), "max_input_bytes", None)
        if input_limit is not None and len(payload) > input_limit:
            raise ValueError("external evaluator request exceeds isolation input budget")
        if self.isolated_executor is not None:
            # The adapter owns OS-level isolation and must preserve the bounded
            # Popen-like stream/termination contract used below.
            process = self.isolated_executor.launch(command, environment)
        else:
            # Available only in explicit test-verifier mode. A normal host
            # subprocess is not a security sandbox.
            process = subprocess.Popen(
                list(command.argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                env=environment,
                start_new_session=(os.name == "posix"),
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
                        _kill_evaluator_process_tree(process)
                        return
                    chunk = process.stdout.read1(min(65_536, remaining))
                    if not chunk:
                        return
                    output.extend(chunk)
                    if len(output) > command.max_output_bytes:
                        output_limit_exceeded.set()
                        # The evaluator may have spawned descendants that keep
                        # running after the direct child is killed. Terminate its
                        # dedicated process group, just as on timeout.
                        _kill_evaluator_process_tree(process)
                        return
            except (OSError, ValueError):
                # Closing a pipe after a timeout/kill is expected.
                return

        def send_request() -> None:
            try:
                process.stdin.write(payload)
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
            _kill_evaluator_process_tree(process)
            process.wait()

        writer.join(timeout=1.0)
        reader.join(timeout=35.0)
        _close_evaluator_streams(process)
        if reader.is_alive():
            raise RuntimeError("external evaluator termination or output drain remains unresolved")
        termination_error = getattr(process, "termination_error", None)
        if termination_error:
            raise RuntimeError(f"external evaluator isolation cleanup failed closed: {termination_error}")
        if output_limit_exceeded.is_set():
            raise ValueError("external evaluator receipt exceeds output budget")
        if timed_out:
            raise RuntimeError("external evaluator timed out")
        if reader.is_alive():
            raise RuntimeError("external evaluator output stream did not close within the bounded wait")
        if process.returncode != 0:
            raise RuntimeError(f"external evaluator failed with exit code {process.returncode}")
        receipt = import_outcome_receipt(bytes(output))
        if self.boundary._replay_ledger is not None:
            return self.boundary._accept_preclaimed(
                request, receipt, replay_ledger=self.replay_ledger
            )
        return self.boundary.accept(request, receipt)


__all__ = ["ExternalEvaluatorCommand", "ExternalEvaluatorGateway"]
