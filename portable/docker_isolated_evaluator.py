"""Docker-backed OS isolation for sealed Arena evaluator processes.

This adapter deliberately requires immutable image digests and a working Docker
daemon. It is Linux-only: unsupported hosts and missing runtime evidence fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
from typing import Sequence


_DIGEST_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class DockerEvaluatorPolicy:
    image: str
    uid: int = 65532
    gid: int = 65532
    memory_limit: str = "512m"
    cpu_limit: str = "1.0"
    pids_limit: int = 128
    tmpfs_size: str = "64m"
    max_input_bytes: int = 1_048_576
    allowed_environment_keys: tuple[str, ...] = ("LANG", "LC_ALL", "TZ")

    def __post_init__(self) -> None:
        if not isinstance(self.image, str) or not _DIGEST_IMAGE.fullmatch(self.image):
            raise ValueError("evaluator image must be pinned by an immutable sha256 digest")
        if os.name != "posix" or not Path("/proc").is_dir():
            raise ValueError("Docker evaluator isolation is supported only on Linux")
        if isinstance(self.uid, bool) or not isinstance(self.uid, int) or self.uid <= 0:
            raise ValueError("evaluator UID must be a non-root positive integer")
        if isinstance(self.gid, bool) or not isinstance(self.gid, int) or self.gid <= 0:
            raise ValueError("evaluator GID must be a non-root positive integer")
        if not re.fullmatch(r"[1-9][0-9]*(?:m|g)", self.memory_limit):
            raise ValueError("memory_limit must be a positive Docker memory quantity")
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", self.cpu_limit) or float(self.cpu_limit) <= 0:
            raise ValueError("cpu_limit must be a positive CPU quantity")
        if isinstance(self.pids_limit, bool) or not isinstance(self.pids_limit, int) or self.pids_limit <= 0:
            raise ValueError("pids_limit must be a positive integer")
        if not re.fullmatch(r"[1-9][0-9]*(?:m|g)", self.tmpfs_size):
            raise ValueError("tmpfs_size must be a positive Docker size")
        if isinstance(self.max_input_bytes, bool) or not isinstance(self.max_input_bytes, int) or self.max_input_bytes <= 0:
            raise ValueError("max_input_bytes must be a positive integer")
        if not isinstance(self.allowed_environment_keys, tuple) or any(
            not isinstance(key, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key)
            or key == "PATH" for key in self.allowed_environment_keys
        ):
            raise ValueError("allowed_environment_keys must be an immutable tuple of safe environment names")
        if len(set(self.allowed_environment_keys)) != len(self.allowed_environment_keys):
            raise ValueError("allowed_environment_keys must be unique")


class _DockerAttachedProcess:
    """Popen-compatible attached client with explicit container termination."""

    def __init__(self, process: subprocess.Popen[bytes], container_id_file: Path, docker_binary: str) -> None:
        self._process = process
        self._container_id_file = container_id_file
        self._docker_binary = docker_binary
        self.stdin = process.stdin
        self.stdout = process.stdout
        self.pid = process.pid
        self.termination_error: str | None = None

    @property
    def container_id(self) -> str | None:
        """Return a validated live-container ID from the private cidfile."""
        try:
            value = self._container_id_file.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return None
        return value if re.fullmatch(r"[0-9a-f]{64}", value) else None

    @property
    def returncode(self):
        return self._process.returncode

    def wait(self, timeout=None):
        return self._process.wait(timeout=timeout)

    def terminate_isolated(self) -> None:
        """Kill the actual container; preserve the cidfile if outcome is unknown."""
        errors: list[str] = []
        try:
            container_id = self._container_id_file.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            container_id = ""
        if not container_id:
            errors.append("container ID unavailable; remote termination outcome is unknown")
        else:
            try:
                killed = subprocess.run(
                    [self._docker_binary, "kill", container_id],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=10, check=False,
                )
                if killed.returncode != 0:
                    state = subprocess.run(
                        [self._docker_binary, "inspect", "--format", "{{.State.Running}}", container_id],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, timeout=10, check=False, text=True,
                    )
                    if state.returncode == 0 and state.stdout.strip().lower() == "true":
                        errors.append("container remained running after kill request")
                    elif state.returncode == 0 and state.stdout.strip().lower() != "false":
                        errors.append("container running state could not be verified")
            except (OSError, subprocess.TimeoutExpired):
                errors.append("container kill or state reconciliation failed")
            try:
                removed = subprocess.run(
                    [self._docker_binary, "rm", "-f", container_id],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=10, check=False,
                )
                if removed.returncode != 0:
                    exists = subprocess.run(
                        [self._docker_binary, "inspect", container_id],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=10, check=False,
                    )
                    if exists.returncode == 0:
                        errors.append("container cleanup failed and container still exists")
            except (OSError, subprocess.TimeoutExpired):
                errors.append("container cleanup could not be verified")
        try:
            if os.name == "posix":
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
        self.termination_error = "; ".join(errors) if errors else None
        if self.termination_error is None:
            try:
                self._container_id_file.unlink(missing_ok=True)
            except OSError:
                self.termination_error = "container ID evidence could not be cleaned up"



class DockerIsolatedEvaluatorExecutor:
    """Run a containerized evaluator with a restrictive, bounded runtime policy."""

    def __init__(self, policy: DockerEvaluatorPolicy, *, docker_binary: str = "docker") -> None:
        if not isinstance(policy, DockerEvaluatorPolicy):
            raise TypeError("a validated DockerEvaluatorPolicy is required")
        if not isinstance(docker_binary, str) or not docker_binary.strip():
            raise ValueError("docker_binary must be a non-empty executable name")
        self.policy = policy
        self.docker_binary = docker_binary

    @property
    def production_ready(self) -> bool:
        """Probe daemon and immutable image availability each time readiness is queried."""
        if os.name != "posix" or not Path("/proc").is_dir():
            return False
        try:
            info = subprocess.run(
                [self.docker_binary, "info", "--format", "{{.OSType}}"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=5, check=False, text=True,
            )
            inspect = subprocess.run(
                [self.docker_binary, "image", "inspect", "--format", "{{index .RepoDigests 0}}", self.policy.image],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=5, check=False, text=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return (
            info.returncode == 0 and info.stdout.strip() == "linux"
            and inspect.returncode == 0
            and self.policy.image in inspect.stdout.strip().splitlines()
        )

    def launch(self, command, environment: dict[str, str]) -> _DockerAttachedProcess:
        if not self.production_ready:
            raise RuntimeError("Docker isolation runtime or pinned image could not be verified")
        if not command.argv:
            raise ValueError("evaluator command must not be empty")
        safe_environment = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
        for key, value in environment.items():
            if key == "PATH":
                continue
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError("invalid evaluator environment variable name")
            if key not in self.policy.allowed_environment_keys:
                raise ValueError(f"evaluator environment variable is not allowlisted: {key}")
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError("invalid evaluator environment value")
            safe_environment[key] = value
        # A private cidfile permits timeout/cancellation to kill the actual
        # container instead of merely killing the attached Docker CLI process.
        fd, cid_path = tempfile.mkstemp(prefix="auren-evaluator-", suffix=".cid")
        os.close(fd)
        cid_file = Path(cid_path)
        cid_file.unlink(missing_ok=True)
        args: list[str] = [
            self.docker_binary, "run", "--rm", "--cidfile", str(cid_file), "--interactive",
            "--network=none", "--user", f"{self.policy.uid}:{self.policy.gid}",
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--pids-limit", str(self.policy.pids_limit), "--memory", self.policy.memory_limit,
            "--cpus", self.policy.cpu_limit,
            "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={self.policy.tmpfs_size}",
        ]
        for key, value in sorted(safe_environment.items()):
            args.extend(["--env", f"{key}={value}"])
        args.append(self.policy.image)
        args.extend(command.argv)
        try:
            process = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                shell=False, start_new_session=True,
            )
        except Exception:
            cid_file.unlink(missing_ok=True)
            raise
        return _DockerAttachedProcess(process, cid_file, self.docker_binary)


__all__ = ["DockerEvaluatorPolicy", "DockerIsolatedEvaluatorExecutor"]
