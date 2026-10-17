"""Subprocess boundary for the Independent Generalization Arena.

The evaluator keeps the sealed manifest and oracle in the parent process. The
policy subprocess receives exactly one JSON task object on stdin and must return
one JSON value on stdout. This is a process boundary, not an OS sandbox: callers
must use a separately controlled container/account or external sandbox for
untrusted code and genuinely independent campaigns.
"""
from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

from .independent_arena import ArenaError, NonceLedger, run_campaign


def _kill_windows_process_tree(process: subprocess.Popen[Any]) -> None:
    """Ask Windows to terminate the process and its descendants, then fall back."""
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5.0,
            check=False,
            shell=False,
            close_fds=True,
        )
        if result.returncode == 0:
            return
    except (OSError, subprocess.TimeoutExpired):
        # taskkill may be unavailable or time out; still kill the direct child.
        pass
    try:
        process.kill()
    except OSError:
        pass


def _kill_policy_process_tree(process: subprocess.Popen[Any]) -> None:
    """Best-effort tree termination; POSIX uses process groups, Windows taskkill."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except OSError:
            # Best-effort fallback if the process group cannot be addressed.
            pass
    elif os.name == "nt":
        _kill_windows_process_tree(process)
        return
    try:
        process.kill()
    except OSError:
        pass


_SENSITIVE_ENV_FRAGMENTS = (
    "ORACLE", "ANSWER", "EVALUATOR", "RECEIPT", "MANIFEST", "SECRET", "TOKEN",
)


def _policy_environment(overrides: Mapping[str, str] | None) -> dict[str, str]:
    """Build a minimal environment without inheriting evaluator credentials."""
    env: dict[str, str] = {}
    for key in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if overrides is not None:
        if not isinstance(overrides, Mapping):
            raise ArenaError("policy_environment must be a mapping")
        for key, value in overrides.items():
            if not isinstance(key, str) or not key or not isinstance(value, str):
                raise ArenaError("policy environment keys and values must be strings")
            if any(fragment in key.upper() for fragment in _SENSITIVE_ENV_FRAGMENTS):
                raise ArenaError(f"refusing sensitive environment variable for policy: {key}")
            env[key] = value
    return env


def run_campaign_isolated(
    sealed: Mapping[str, Any],
    policy_command: Sequence[str],
    *,
    policy_digest: str,
    run_nonce: str,
    nonce_ledger: NonceLedger,
    timeout_seconds: float = 10.0,
    campaign_timeout_seconds: float = 120.0,
    max_output_bytes: int = 65_536,
    policy_environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute each case in a fresh policy subprocess.

    The command is invoked without a shell. Only the task-facing JSON object is
    sent to stdin; stdout must contain exactly one JSON value. Environment
    inheritance is allowlisted, stderr and exception details are never placed
    in receipts. Per-case and whole-campaign deadlines bound runtime; stdout is
    redirected to a temporary file and rejected above max_output_bytes to avoid
    unbounded in-memory capture. This is still not an OS sandbox.
    """
    if isinstance(policy_command, (str, bytes)) or not isinstance(policy_command, Sequence) or not policy_command:
        raise ArenaError("policy_command must be a non-empty argument sequence")
    if any(not isinstance(arg, str) or not arg for arg in policy_command):
        raise ArenaError("policy_command arguments must be non-empty strings")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ArenaError("timeout_seconds must be a finite positive number")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ArenaError("timeout_seconds must be a finite positive number")
    for name, value in (("campaign_timeout_seconds", campaign_timeout_seconds),):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ArenaError(f"{name} must be a finite positive number")
    if type(max_output_bytes) is not int or max_output_bytes < 1:
        raise ArenaError("max_output_bytes must be a positive integer")
    env = _policy_environment(policy_environment)
    deadline = time.monotonic() + float(campaign_timeout_seconds)

    with tempfile.TemporaryDirectory(prefix="auren-arena-policy-") as workdir:
        def policy(task: Mapping[str, str]) -> Any:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ArenaError("Arena campaign execution budget exhausted")
            timeout = min(float(timeout_seconds), remaining)
            request = json.dumps(
                dict(task), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            )
            with tempfile.TemporaryFile(mode="w+b") as output:
                process = subprocess.Popen(
                    list(policy_command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, cwd=workdir, env=env, shell=False,
                    close_fds=True, start_new_session=(os.name == "posix"),
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
                )
                output_limit_exceeded = threading.Event()
                stream_error: list[BaseException] = []

                def drain_output() -> None:
                    total = 0
                    try:
                        assert process.stdout is not None
                        while True:
                            chunk = process.stdout.read(8192)
                            if not chunk:
                                return
                            total += len(chunk)
                            if total > max_output_bytes:
                                output_limit_exceeded.set()
                                _kill_policy_process_tree(process)
                                return
                            output.write(chunk)
                    except (OSError, ValueError) as exc:
                        stream_error.append(exc)

                def send_request() -> None:
                    try:
                        assert process.stdin is not None
                        process.stdin.write(request.encode("utf-8"))
                        process.stdin.close()
                    except (BrokenPipeError, OSError, ValueError):
                        return

                reader = threading.Thread(target=drain_output, daemon=True)
                writer = threading.Thread(target=send_request, daemon=True)
                reader.start()
                writer.start()
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    _kill_policy_process_tree(process)
                    process.wait()
                    # The process-group signal can race with a descendant being
                    # created as the parent handles SIGTERM/SIGKILL on a loaded
                    # runner. Reap the leader, then signal the same group again
                    # before returning so late descendants cannot outlive timeout.
                    if os.name == "posix":
                        _kill_policy_process_tree(process)
                    writer.join(timeout=1.0)
                    reader.join(timeout=1.0)
                    if deadline - time.monotonic() <= 0:
                        raise ArenaError("Arena campaign execution budget exhausted") from exc
                    raise ArenaError("policy subprocess exceeded its per-case timeout") from exc
                writer.join(timeout=1.0)
                reader.join(timeout=1.0)
                if output_limit_exceeded.is_set():
                    raise ArenaError("policy subprocess output exceeds configured byte limit")
                if reader.is_alive():
                    _kill_policy_process_tree(process)
                    raise ArenaError("policy output stream did not close within the bounded wait")
                if stream_error:
                    raise ArenaError("policy output stream could not be read")
                if process.returncode != 0:
                    raise ArenaError("policy subprocess returned a non-zero exit status")
                output.seek(0)
                try:
                    return json.loads(output.read().decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
                    raise ArenaError("policy subprocess did not return one valid JSON value") from exc

        return run_campaign(
            sealed,
            policy,
            policy_digest=policy_digest,
            run_nonce=run_nonce,
            nonce_ledger=nonce_ledger,
        )


__all__ = ["run_campaign_isolated"]
