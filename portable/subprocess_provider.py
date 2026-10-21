"""Killable subprocess boundary for providers that support a command-line adapter.

Use this callable with ProviderBackedCrewAdapter. It does not inherit the host
environment, does not invoke a shell, and kills the child process on timeout.
On POSIX it kills the complete process group. On Windows it terminates the
direct child; deployments that allow provider-created descendants need a Job
Object or an equivalent host-level process supervisor.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Mapping, Sequence


class SubprocessProviderError(RuntimeError):
    """Raised when a command-backed provider exits unsuccessfully."""


class SubprocessProviderInvoker:
    """Callable provider adapter with a hard child-process timeout.

    The executable path and working directory are explicit. The child receives
    only the configured environment plus a minimal platform runtime environment.
    No shell is used. The response is UTF-8 text and is checked against a byte cap.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path,
        env: Mapping[str, str] | None = None,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        if isinstance(command, (str, bytes)) or not isinstance(command, Sequence) or not command:
            raise ValueError("command must be a non-empty argument sequence")
        if any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command):
            raise ValueError("command arguments must be non-empty strings without NUL")
        executable = Path(command[0])
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError("command executable must be an existing absolute file path")
        workdir = Path(cwd).resolve()
        if not workdir.is_dir():
            raise ValueError("cwd must be an existing directory")
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 16_000_000:
            raise ValueError("max_response_bytes must be between 1 and 16000000")
        clean_env: dict[str, str] = {"PATH": os.defpath}
        if os.name == "nt":
            for key in ("SystemRoot", "WINDIR", "TEMP", "TMP"):
                value = os.environ.get(key)
                if value:
                    clean_env[key] = value
        for key, value in (env or {}).items():
            if (
                not isinstance(key, str)
                or not key
                or "=" in key
                or "\x00" in key
                or not isinstance(value, str)
                or "\x00" in value
            ):
                raise ValueError("env must contain valid string keys and values")
            clean_env[key] = value
        self.command = tuple(command)
        self.cwd = workdir
        self.env = clean_env
        self.max_response_bytes = max_response_bytes

    @staticmethod
    def _kill(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        except OSError:
            try:
                process.kill()
            except OSError:
                pass

    def __call__(self, prompt: str, timeout_seconds: int) -> str:
        if not isinstance(prompt, str):
            raise TypeError("provider prompt must be text")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        # Leave time for the outer bounded-wait wrapper to observe the result.
        timeout = max(0.01, float(timeout_seconds) - min(0.2, float(timeout_seconds) * 0.1))
        kwargs: dict[str, object] = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True
        elif os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(
            self.command,
            cwd=str(self.cwd),
            env=dict(self.env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            **kwargs,
        )
        try:
            stdout, _stderr = process.communicate(prompt.encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self._kill(process)
            try:
                process.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._kill(process)
            raise TimeoutError("provider subprocess exceeded its deadline and was terminated") from exc
        if process.returncode != 0:
            raise SubprocessProviderError(f"provider subprocess exited with code {process.returncode}")
        if len(stdout) > self.max_response_bytes:
            raise ValueError("provider response exceeds configured byte limit")
        try:
            return stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("provider response must be UTF-8 text") from exc
