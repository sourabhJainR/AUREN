"""Docker-backed external evaluator executor with fail-closed policy probing.

This adapter is intentionally Linux/Docker-only. It is not production-ready until
an explicit probe command in the pinned evaluator image proves the runtime
policy. A configured image or a successful `docker info` call alone is not
evidence of isolation.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Any

_IMAGE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:)?sha256:([0-9a-f]{64})$")
_REQUIRED_PROBES = {
    "non_root", "root_read_only", "network_disabled", "tmpfs_hardened",
    "capabilities_dropped", "no_new_privileges",
}


class ContainerIsolationError(RuntimeError):
    """The container runtime or isolation proof is missing or inconclusive."""


@dataclass(frozen=True)
class ContainerIsolationEvidence:
    image_id: str
    probe_results: dict[str, bool]
    engine_version: str

    @property
    def passed(self) -> bool:
        return bool(self.image_id) and all(self.probe_results.get(key) is True for key in _REQUIRED_PROBES)


class _ContainerProcess:
    """Popen-like client wrapper that also removes the named container on kill."""

    def __init__(self, runtime: str, name: str, argv: list[str]) -> None:
        self.runtime = runtime
        self.name = name
        self._process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, shell=False, close_fds=True,
            start_new_session=(os.name == "posix"),
        )
        self.stdin = self._process.stdin
        self.stdout = self._process.stdout
        self.pid = self._process.pid
        self.returncode: int | None = None
        self.termination_error: str | None = None
        self._lock = threading.Lock()

    def wait(self, timeout: float | None = None) -> int:
        try:
            self.returncode = self._process.wait(timeout=timeout)
            return self.returncode
        except subprocess.TimeoutExpired:
            raise
        finally:
            if self._process.poll() is not None:
                self.returncode = self._process.returncode

    def terminate_isolated(self) -> None:
        """Stop the container, reconcile its removal, then stop the attached client.

        The gateway calls this method on timeout or output-limit breach. Killing
        only the Docker CLI is not sufficient because the evaluator container
        may continue running after the client exits.
        """
        errors: list[str] = []
        try:
            killed = subprocess.run(
                [self.runtime, "kill", self.name], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, check=False, shell=False, close_fds=True,
            )
            if killed.returncode != 0:
                state = subprocess.run(
                    [self.runtime, "inspect", "--format", "{{.State.Running}}", self.name],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=5, check=False,
                    shell=False, close_fds=True, text=True,
                )
                if state.returncode != 0 or state.stdout.strip().lower() == "true":
                    errors.append("container termination could not be confirmed")
        except (OSError, subprocess.TimeoutExpired):
            errors.append("container termination request failed")
        try:
            removed = subprocess.run(
                [self.runtime, "rm", "--force", self.name], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, check=False, shell=False, close_fds=True,
            )
            if removed.returncode != 0:
                exists = subprocess.run(
                    [self.runtime, "inspect", self.name], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, check=False, shell=False, close_fds=True,
                )
                if exists.returncode == 0:
                    errors.append("container cleanup failed and container still exists")
        except (OSError, subprocess.TimeoutExpired):
            errors.append("container cleanup could not be verified")
        try:
            if os.name == "posix":
                import signal
                os.killpg(self._process.pid, signal.SIGKILL)
            else:
                self._process.kill()
        except (ProcessLookupError, PermissionError, OSError):
            try:
                self._process.kill()
            except (ProcessLookupError, OSError):
                pass
        try:
            self._process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            errors.append("Docker client process did not stop within the bounded wait")
        self.returncode = self._process.returncode
        self.termination_error = "; ".join(errors) if errors else None

    def kill(self) -> None:
        """Compatibility alias; cleanup status is exposed through termination_error."""
        self.terminate_isolated()

    def poll(self) -> int | None:
        self.returncode = self._process.poll()
        return self.returncode


class DockerExternalEvaluatorExecutor:
    """Enforce a fixed Docker policy for evaluator commands.

    The image must be an immutable digest or local image ID. The image's
    `--auren-isolation-probe` command must emit a JSON object with all required
    probe names set to true. Production readiness is granted only after that
    probe succeeds. This is a policy gate, not a claim that Docker protects
    against a compromised host kernel or daemon.
    """

    production_ready = False

    def __init__(
        self,
        *,
        image: str,
        runtime: str = "docker",
        probe_timeout_seconds: float = 10.0,
    ) -> None:
        if os.name != "posix" or not os.path.exists("/proc/self/status"):
            raise ContainerIsolationError("production evaluator isolation currently requires Linux")
        if not isinstance(image, str) or not _IMAGE.fullmatch(image):
            raise ContainerIsolationError("image must be pinned to an immutable sha256 digest or image ID")
        if not isinstance(runtime, str) or not runtime.strip() or os.path.basename(runtime) not in {"docker"}:
            raise ContainerIsolationError("only the explicitly supported Docker runtime is accepted")
        executable = shutil.which(runtime)
        if executable is None:
            raise ContainerIsolationError("Docker is unavailable; host fallback is forbidden")
        self.runtime = executable
        self.image = image
        self.probe_timeout_seconds = float(probe_timeout_seconds)
        self.evidence: ContainerIsolationEvidence | None = None

    def verify_policy(self) -> ContainerIsolationEvidence:
        try:
            version = subprocess.run(
                [self.runtime, "version", "--format", "{{.Server.Version}}"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=self.probe_timeout_seconds, check=True, text=True, shell=False,
            ).stdout.strip()
            inspected = subprocess.run(
                [self.runtime, "image", "inspect", self.image, "--format", "{{.Id}}"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=self.probe_timeout_seconds, check=True, text=True, shell=False,
            ).stdout.strip()
            probe = subprocess.run(
                [self.runtime, "run", "--rm", "--pull=never", "--network=none",
                 "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                 "--pids-limit=64", "--memory=256m", "--memory-swap=256m",
                 "--cpus=1", "--user=65534:65534",
                 "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m",
                 self.image, "--auren-isolation-probe"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=self.probe_timeout_seconds, check=True, text=True, shell=False,
            )
            result = json.loads(probe.stdout)
            if not isinstance(result, dict):
                raise ValueError("probe result is not an object")
            checks = {key: result.get(key) is True for key in _REQUIRED_PROBES}
            evidence = ContainerIsolationEvidence(inspected, checks, version)
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
            self.production_ready = False
            self.evidence = None
            raise ContainerIsolationError("container isolation policy could not be proven") from exc
        if not evidence.passed:
            self.production_ready = False
            self.evidence = evidence
            raise ContainerIsolationError("one or more required container isolation probes failed")
        self.evidence = evidence
        self.production_ready = True
        return evidence

    def launch(self, command: Any, environment: dict[str, str]) -> _ContainerProcess:
        if self.production_ready is not True or self.evidence is None or not self.evidence.passed:
            raise ContainerIsolationError("verify_policy() must pass before production execution")
        if not isinstance(command.argv, tuple) or not command.argv:
            raise ContainerIsolationError("invalid evaluator command")
        # Do not pass arbitrary host environment through the boundary. Only the
        # explicitly documented non-secret encoding variables are permitted.
        allowed = {"PYTHONIOENCODING", "PYTHONUTF8"}
        if any(key not in allowed for key in environment if key != "PATH"):
            raise ContainerIsolationError("evaluator environment contains an unsupported key")
        from uuid import uuid4
        name = "auren-external-evaluator-" + uuid4().hex
        argv = [
            self.runtime, "run", "--interactive", "--rm", "--name", name,
            "--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory=256m", "--memory-swap=256m", "--cpus=1",
            "--user=65534:65534",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--env=PYTHONIOENCODING=utf-8", "--env=PYTHONUTF8=1",
            self.image, *command.argv,
        ]
        try:
            return _ContainerProcess(self.runtime, name, argv)
        except OSError as exc:
            raise ContainerIsolationError("Docker evaluator container could not be launched") from exc


__all__ = ["ContainerIsolationEvidence", "ContainerIsolationError", "DockerExternalEvaluatorExecutor"]
