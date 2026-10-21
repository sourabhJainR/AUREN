"""Killable subprocess boundary for command-backed providers.

No shell is used. The child does not inherit the host environment. Output is
read incrementally with a strict byte cap. POSIX timeouts kill the whole process
group. On Windows, the direct child is terminated; deployments that permit
provider-created descendants must use a Job Object or equivalent supervisor.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Mapping, Sequence


class SubprocessProviderError(RuntimeError):
    """Raised when a command-backed provider exits unsuccessfully."""


class SubprocessProviderInvoker:
    """Invoke a provider in a bounded child process with explicit permissions."""

    def __init__(self, command: Sequence[str], *, cwd: str | Path,
                 env: Mapping[str, str] | None = None,
                 max_response_bytes: int = 1_000_000,
                 max_prompt_bytes: int = 1_000_000) -> None:
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
        for name, value in (("max_response_bytes", max_response_bytes), ("max_prompt_bytes", max_prompt_bytes)):
            if type(value) is not int or not 1 <= value <= 16_000_000:
                raise ValueError(f"{name} must be between 1 and 16000000")
        clean_env: dict[str, str] = {"PATH": os.defpath}
        if os.name == "nt":
            for key in ("SystemRoot", "WINDIR", "TEMP", "TMP"):
                value = os.environ.get(key)
                if value:
                    clean_env[key] = value
        for key, value in (env or {}).items():
            if not isinstance(key, str) or not key or "=" in key or "\x00" in key or not isinstance(value, str) or "\x00" in value:
                raise ValueError("env must contain valid string keys and values")
            clean_env[key] = value
        self.command = tuple(command)
        self.cwd = workdir
        self.env = clean_env
        self.max_response_bytes = max_response_bytes
        self.max_prompt_bytes = max_prompt_bytes

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

    def __call__(self, prompt: str, timeout_seconds: int | float) -> str:
        if not isinstance(prompt, str):
            raise TypeError("provider prompt must be text")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        payload = prompt.encode("utf-8")
        if len(payload) > self.max_prompt_bytes:
            raise ValueError("provider prompt exceeds configured byte limit")
        timeout = float(timeout_seconds)
        started = time.monotonic()
        kwargs: dict[str, object] = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True
        elif os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(
            self.command, cwd=str(self.cwd), env=dict(self.env),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            shell=False, bufsize=0, **kwargs,
        )
        output = bytearray()
        overflow = threading.Event()
        read_error: list[BaseException] = []

        def read_stdout() -> None:
            assert process.stdout is not None
            try:
                while True:
                    chunk = process.stdout.read(8192)
                    if not chunk:
                        return
                    remaining = self.max_response_bytes + 1 - len(output)
                    output.extend(chunk[:max(0, remaining)])
                    if len(chunk) > remaining or len(output) > self.max_response_bytes:
                        overflow.set()
                        self._kill(process)
                        return
            except BaseException as exc:
                read_error.append(exc)
                self._kill(process)

        def write_stdin() -> None:
            try:
                assert process.stdin is not None
                process.stdin.write(payload)
                process.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

        reader = threading.Thread(target=read_stdout, name="auren-provider-stdout", daemon=True)
        writer = threading.Thread(target=write_stdin, name="auren-provider-stdin", daemon=True)
        reader.start()
        writer.start()
        remaining = timeout - (time.monotonic() - started)
        try:
            process.wait(timeout=max(0.001, remaining))
        except subprocess.TimeoutExpired as exc:
            self._kill(process)
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._kill(process)
            raise TimeoutError("provider subprocess exceeded its deadline and was terminated") from exc
        writer.join(timeout=0.2)
        reader.join(timeout=max(0.0, timeout - (time.monotonic() - started)))
        if overflow.is_set():
            raise ValueError("provider response exceeds configured byte limit")
        if read_error:
            raise SubprocessProviderError("provider response stream failed") from read_error[0]
        if reader.is_alive():
            self._kill(process)
            raise TimeoutError("provider output stream did not close before its deadline")
        if process.returncode != 0:
            raise SubprocessProviderError(f"provider subprocess exited with code {process.returncode}")
        try:
            return bytes(output).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("provider response must be UTF-8 text") from exc
