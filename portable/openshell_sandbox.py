"""Optional OpenShell sandbox backend for host-owned coding/test commands.

No local execution fallback is provided. The caller supplies a curated workspace,
explicit policy file, command, and optional credential-provider names. Raw output
is returned to the caller but excluded from the evidence projection by default.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Sequence
from uuid import uuid4


_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


class OpenShellUnavailableError(RuntimeError):
    """OpenShell is not installed or cannot be invoked; local fallback is forbidden."""


@dataclass(frozen=True)
class OpenShellExecutionRequest:
    name_prefix: str
    workspace: str
    policy_file: str
    command: tuple[str, ...]
    providers: tuple[str, ...] = ()
    timeout_seconds: float = 600.0

    def validate(self) -> tuple[Path, Path]:
        if not isinstance(self.name_prefix, str) or not _NAME.fullmatch(self.name_prefix):
            raise ValueError("name_prefix must be a lowercase sandbox-safe identifier")
        if not isinstance(self.command, tuple) or not self.command:
            raise ValueError("command must be a non-empty tuple of arguments")
        for arg in self.command:
            if not isinstance(arg, str) or not arg.strip() or "\x00" in arg:
                raise ValueError("command arguments must be non-empty strings without NUL bytes")
        if not isinstance(self.providers, tuple):
            raise ValueError("providers must be a tuple of provider names")
        for provider in self.providers:
            if not isinstance(provider, str) or not _NAME.fullmatch(provider):
                raise ValueError("provider names must be lowercase sandbox-safe identifiers")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a finite positive number")
        workspace = Path(self.workspace).expanduser().resolve()
        policy = Path(self.policy_file).expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError("workspace must be an existing curated directory")
        if not policy.is_file():
            raise ValueError("an explicit OpenShell policy file is required")
        if not policy.read_bytes().strip():
            raise ValueError("OpenShell policy file must not be empty")
        return workspace, policy


@dataclass(frozen=True)
class OpenShellExecutionResult:
    sandbox_name: str
    exit_code: int
    timed_out: bool
    duration_seconds: float
    policy_digest: str
    stdout: str
    stderr: str
    cleanup_attempted: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def as_evidence(self) -> dict[str, Any]:
        """Return a low-leakage receipt suitable for AUREN's evidence graph."""
        return {
            "backend": "nvidia-openshell",
            "sandbox_name": self.sandbox_name,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "passed": self.passed,
            "duration_seconds": round(self.duration_seconds, 6),
            "policy_digest": self.policy_digest,
            "stdout_digest": sha256(self.stdout.encode("utf-8", errors="replace")).hexdigest(),
            "stderr_digest": sha256(self.stderr.encode("utf-8", errors="replace")).hexdigest(),
            "stdout_bytes": len(self.stdout.encode("utf-8", errors="replace")),
            "stderr_bytes": len(self.stderr.encode("utf-8", errors="replace")),
            "cleanup_attempted": self.cleanup_attempted,
        }


class OpenShellSandboxBackend:
    """Run a trusted command in an ephemeral OpenShell sandbox with no fallback."""

    def __init__(
        self,
        *,
        executable: str = "openshell",
        runner: Callable[..., Any] = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._executable = executable
        self._runner = runner
        self._which = which
        self._monotonic = monotonic

    def execute(self, request: OpenShellExecutionRequest) -> OpenShellExecutionResult:
        workspace, policy = request.validate()
        executable = self._which(self._executable)
        if not executable:
            raise OpenShellUnavailableError(
                "OpenShell CLI is unavailable; refusing to execute this task on the host"
            )
        try:
            policy_digest = sha256(policy.read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError("OpenShell policy file could not be read") from exc

        sandbox_name = f"{request.name_prefix}-{uuid4().hex[:12]}"
        args: list[str] = [
            executable, "sandbox", "create",
            "--name", sandbox_name,
            "--policy", str(policy),
            "--upload", f"{workspace}:/workspace",
            "--no-keep",
        ]
        for provider in request.providers:
            args.extend(("--provider", provider))
        args.extend(("--", *request.command))

        started = self._monotonic()
        try:
            completed = self._runner(
                args,
                capture_output=True,
                text=True,
                timeout=float(request.timeout_seconds),
                check=False,
            )
            return OpenShellExecutionResult(
                sandbox_name=sandbox_name,
                exit_code=int(completed.returncode),
                timed_out=False,
                duration_seconds=max(0.0, self._monotonic() - started),
                policy_digest=policy_digest,
                stdout=self._text_output(getattr(completed, "stdout", "")),
                stderr=self._text_output(getattr(completed, "stderr", "")),
            )
        except subprocess.TimeoutExpired as exc:
            cleaned = self._cleanup(executable, sandbox_name, request.timeout_seconds)
            return OpenShellExecutionResult(
                sandbox_name=sandbox_name,
                exit_code=124,
                timed_out=True,
                duration_seconds=max(0.0, self._monotonic() - started),
                policy_digest=policy_digest,
                stdout=self._text_output(exc.stdout),
                stderr=self._text_output(exc.stderr),
                cleanup_attempted=cleaned,
            )
        except OSError as exc:
            raise OpenShellUnavailableError(
                "OpenShell command could not be invoked; refusing host fallback"
            ) from exc

    def _cleanup(self, executable: str, sandbox_name: str, timeout_seconds: float) -> bool:
        try:
            result = self._runner(
                [executable, "sandbox", "delete", sandbox_name],
                capture_output=True,
                text=True,
                timeout=min(10.0, float(timeout_seconds)),
                check=False,
            )
            return int(result.returncode) == 0
        except Exception:
            return False

    @staticmethod
    def _text_output(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)


__all__ = [
    "OpenShellExecutionRequest",
    "OpenShellExecutionResult",
    "OpenShellSandboxBackend",
    "OpenShellUnavailableError",
]
