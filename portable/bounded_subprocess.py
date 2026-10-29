"""Bounded subprocess execution with process-tree termination."""
from __future__ import annotations
from dataclasses import dataclass
import math, os, signal, subprocess, threading, time
from typing import Mapping, Sequence
from .bounded_capture import BoundedStreamCapture
from .agent_execution_guard import deny_unscoped_subprocess_for_assigned_agent

@dataclass(frozen=True, slots=True)
class BoundedCommandResult:
    status: str
    returncode: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    elapsed_seconds: float
    reason: str | None = None
    @property
    def succeeded(self) -> bool:
        return self.status == "completed" and self.returncode == 0

def _positive(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return float(value)

def _tail(stream, limit: int) -> tuple[str, bool]:
    stream.flush(); stream.seek(0, os.SEEK_END); size = stream.tell()
    stream.seek(max(0, size - limit)); data = stream.read(limit)
    return data.decode("utf-8", errors="replace"), size > limit

def _group_exists(pgid: int) -> bool:
    if os.name == "nt": return False
    try: os.killpg(pgid, 0); return True
    except ProcessLookupError: return False
    except PermissionError: return True

def _kill_tree(process: subprocess.Popen, grace: float) -> None:
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=max(1.0, grace + 1.0), check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired):
            try: process.kill()
            except OSError: pass
    else:
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        end = time.monotonic() + grace
        while time.monotonic() < end and _group_exists(process.pid):
            time.sleep(min(0.02, max(0.0, end - time.monotonic())))
        try: os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError): pass
    try: process.wait(timeout=max(1.0, grace + 1.0))
    except subprocess.TimeoutExpired:
        try: process.kill()
        except OSError: pass
        process.wait(timeout=2.0)

def run_bounded_command(
    argv: Sequence[str], *, timeout_seconds: float,
    max_memory_bytes: int | None = None,
    max_processes: int | None = None,
    deadline_monotonic: float | None = None,
    cancellation_event: threading.Event | None = None,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    max_output_bytes: int = 65536,
    terminate_grace_seconds: float = 0.25,
    poll_interval_seconds: float = 0.02,
) -> BoundedCommandResult:
    """Run argv without a shell; inherit parent deadline and bound output."""
    # This is a direct Popen primitive; apply the assignment guard here as well.
    # No cwd or output limit proves the child is confined to delegated scope.
    deny_unscoped_subprocess_for_assigned_agent()
    if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence) or not argv:
        raise ValueError("argv must be a non-empty argument sequence; shell strings are rejected")
    if any(not isinstance(part, str) or not part for part in argv):
        raise ValueError("every argv item must be non-empty text")
    timeout = _positive("timeout_seconds", timeout_seconds)
    grace = _positive("terminate_grace_seconds", terminate_grace_seconds)
    poll = _positive("poll_interval_seconds", poll_interval_seconds)
    if type(max_output_bytes) is not int or max_output_bytes < 1:
        raise ValueError("max_output_bytes must be a positive integer")
    if max_memory_bytes is not None and (type(max_memory_bytes) is not int or max_memory_bytes < 16 * 1024 * 1024):
        raise ValueError("max_memory_bytes must be an integer of at least 16 MiB")
    if max_processes is not None and (type(max_processes) is not int or max_processes < 1):
        raise ValueError("max_processes must be a positive integer")
    if deadline_monotonic is not None and (isinstance(deadline_monotonic, bool) or not isinstance(deadline_monotonic, (int, float)) or not math.isfinite(deadline_monotonic)):
        raise ValueError("deadline_monotonic must be finite")
    started = time.monotonic()
    if cancellation_event is not None and cancellation_event.is_set():
        return BoundedCommandResult("cancelled", None, "", "", False, False, 0.0, "cancellation_requested")
    if deadline_monotonic is not None and deadline_monotonic <= started:
        return BoundedCommandResult("timed_out", None, "", "", False, False, 0.0, "parent_deadline_exceeded")
    local_deadline = started + timeout
    end = min(local_deadline, deadline_monotonic) if deadline_monotonic is not None else local_deadline
    reason_on_timeout = "parent_deadline_exceeded" if deadline_monotonic is not None and deadline_monotonic <= local_deadline else "command_timeout"
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(cwd) if cwd is not None else None,
                   env=dict(env) if env is not None else None, shell=False, close_fds=True)
    if max_memory_bytes is not None and os.name != "nt":
        # RLIMIT_AS is per-process, inherited by descendants; not aggregate container isolation.
        import resource
        if not hasattr(resource, "RLIMIT_AS"):
            raise RuntimeError("memory_limit_unavailable: RLIMIT_AS is not supported")
        def apply_resource_limits():
            resource.setrlimit(resource.RLIMIT_AS, (max_memory_bytes, max_memory_bytes))
            if max_processes is not None:
                if not hasattr(resource, "RLIMIT_NPROC"):
                    raise RuntimeError("process_limit_unavailable: RLIMIT_NPROC is not supported")
                resource.setrlimit(resource.RLIMIT_NPROC, (max_processes, max_processes))
        options["preexec_fn"] = apply_resource_limits
    if max_processes is not None and os.name != "nt" and max_memory_bytes is None:
        import resource
        if not hasattr(resource, "RLIMIT_NPROC"):
            raise RuntimeError("process_limit_unavailable: RLIMIT_NPROC is not supported")
        def apply_process_limit():
            resource.setrlimit(resource.RLIMIT_NPROC, (max_processes, max_processes))
        options["preexec_fn"] = apply_process_limit
    job = None
    if os.name == "nt":
        from .windows_kill_job import CREATE_SUSPENDED, WindowsKillJob
        options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | CREATE_SUSPENDED
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(list(argv), **options)
    if os.name == "nt":
        try:
            job = WindowsKillJob(process, max_memory_bytes=max_memory_bytes, max_processes=max_processes)
        except BaseException:
            _kill_tree(process, grace)
            raise
    assert process.stdout is not None and process.stderr is not None
    out_capture = BoundedStreamCapture(process.stdout, max_output_bytes)
    err_capture = BoundedStreamCapture(process.stderr, max_output_bytes)
    out_capture.start(); err_capture.start()
    status, reason = "completed", None
    def terminate():
        if job is not None:
            try: job.terminate()
            except OSError: pass
        _kill_tree(process, grace)
    while process.poll() is None:
        if cancellation_event is not None and cancellation_event.is_set():
            status, reason = "cancelled", "cancellation_requested"; terminate(); break
        remaining = end - time.monotonic()
        if remaining <= 0:
            status, reason = "timed_out", reason_on_timeout; terminate(); break
        if cancellation_event is not None: cancellation_event.wait(min(poll, remaining))
        else: time.sleep(min(poll, remaining))
    if os.name != "nt" and status == "completed" and _group_exists(process.pid):
        while _group_exists(process.pid) and time.monotonic() < end:
            if cancellation_event is not None and cancellation_event.is_set():
                status, reason = "cancelled", "cancellation_requested"; break
            time.sleep(min(poll, max(0.0, end - time.monotonic())))
        if _group_exists(process.pid):
            status, reason = (("cancelled", "cancellation_requested") if cancellation_event and cancellation_event.is_set() else ("timed_out", reason_on_timeout))
            _kill_tree(process, grace)
    if os.name == "nt" and job is not None and status == "completed":
        try:
            while job.active_processes() > 0 and time.monotonic() < end:
                if cancellation_event is not None and cancellation_event.is_set():
                    status, reason = "cancelled", "cancellation_requested"; break
                time.sleep(min(poll, max(0.0, end - time.monotonic())))
            if job.active_processes() > 0:
                status, reason = (("cancelled", "cancellation_requested") if cancellation_event and cancellation_event.is_set() else ("timed_out", reason_on_timeout))
                job.terminate()
        except OSError:
            status, reason = "failed", "windows_job_accounting_unavailable"
            job.terminate()
    out_closed = out_capture.join(0.5)
    err_closed = err_capture.join(0.5)
    if not out_closed or not err_closed:
        if job is not None:
            try: job.terminate()
            except OSError: pass
        if os.name != "nt": _kill_tree(process, grace)
        out_closed = out_capture.join(0.5)
        err_closed = err_capture.join(0.5)
        if not out_closed or not err_closed:
            status, reason = "timed_out", "output_pipe_did_not_close"
    stdout, out_cut = out_capture.result()
    stderr, err_cut = err_capture.result()
    result = BoundedCommandResult(status, process.poll(), stdout, stderr, out_cut, err_cut, max(0.0, time.monotonic()-started), reason)
    if job is not None: job.close()
    return result

__all__ = ["BoundedCommandResult", "run_bounded_command"]
