"""Killable subprocess boundary for command-backed providers.

No shell is used. The child does not inherit the host environment. Output is
read incrementally with a strict byte cap. POSIX invocations use a process group;
Windows invocations use a kill-on-close Job Object. Descendants are terminated
on cancellation, timeout, output failure, and normal provider exit.
"""
from __future__ import annotations

import math
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Mapping, Sequence


class SubprocessProviderError(RuntimeError):
    """Raised when a command-backed provider exits unsuccessfully."""





def _resume_suspended_windows_process(process: subprocess.Popen[bytes], kernel32) -> None:
    """Resume the suspended initial thread without relying on private Popen fields."""
    import ctypes
    from ctypes import wintypes

    class ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", ctypes.c_long),
            ("tpDeltaPri", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(ThreadEntry32))
    kernel32.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(ThreadEntry32))
    kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenThread.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "could not enumerate provider process threads")
    try:
        entry = ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32OwnerProcessID == process.pid:
                thread_handle = kernel32.OpenThread(0x0002, False, entry.th32ThreadID)
                if thread_handle:
                    try:
                        if kernel32.ResumeThread(thread_handle) == 0xFFFFFFFF:
                            raise OSError(ctypes.get_last_error(), "could not resume provider process")
                        return
                    finally:
                        kernel32.CloseHandle(thread_handle)
            found = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    raise OSError("provider initial thread could not be found for resume")


def _windows_job(process: subprocess.Popen[bytes], *, max_memory_bytes: int | None = None, max_processes: int | None = None):
    """Assign a suspended Windows child to a kill-on-close Job Object."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    )
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        error = ctypes.get_last_error()
        process.kill()
        process.wait()
        raise OSError(error, "could not create provider process Job Object")
    limits = ExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if max_processes is not None:
        limits.BasicLimitInformation.LimitFlags |= 0x00000008  # JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        limits.BasicLimitInformation.ActiveProcessLimit = max_processes
    if max_memory_bytes is not None:
        limits.BasicLimitInformation.LimitFlags |= 0x00000200  # JOB_OBJECT_LIMIT_JOB_MEMORY
        limits.JobMemoryLimit = max_memory_bytes
    try:
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits),
        ):
            raise OSError(ctypes.get_last_error(), "could not configure provider Job Object")
        if not kernel32.AssignProcessToJobObject(handle, process._handle):
            raise OSError(ctypes.get_last_error(), "could not assign provider to Job Object")
        _resume_suspended_windows_process(process, kernel32)
        setattr(process, "_auren_job_handle", handle)
        setattr(process, "_auren_kernel32", kernel32)
        return handle
    except BaseException:
        try:
            kernel32.TerminateJobObject(handle, 1)
        except Exception:
            pass
        kernel32.CloseHandle(handle)
        try:
            process.kill()
            process.wait(timeout=1)
        except Exception:
            pass
        raise


def _close_windows_job(process: subprocess.Popen[bytes]) -> None:
    handle = getattr(process, "_auren_job_handle", None)
    kernel32 = getattr(process, "_auren_kernel32", None)
    if handle and kernel32:
        # KILL_ON_JOB_CLOSE prevents descendants from surviving a successful
        # provider response even when the original process has already exited.
        kernel32.CloseHandle(handle)
        setattr(process, "_auren_job_handle", None)


class SubprocessProviderInvoker:
    """Invoke a provider in a bounded child process with explicit permissions."""

    def __init__(self, command: Sequence[str], *, cwd: str | Path,
                 env: Mapping[str, str] | None = None,
                 max_response_bytes: int = 1_000_000,
                 max_prompt_bytes: int = 1_000_000,
                 max_memory_bytes: int | None = None,
                 max_processes: int | None = None) -> None:
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
        if max_memory_bytes is not None and (type(max_memory_bytes) is not int or max_memory_bytes < 16 * 1024 * 1024):
            raise ValueError("max_memory_bytes must be an integer of at least 16 MiB")
        if max_processes is not None and (type(max_processes) is not int or max_processes < 1):
            raise ValueError("max_processes must be a positive integer")
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
        self.max_memory_bytes = max_memory_bytes
        self.max_processes = max_processes

    @staticmethod
    def _kill(process: subprocess.Popen[bytes]) -> None:
        # The leader can exit while descendants remain. Kill the entire POSIX
        # group, or the Windows Job Object, even after process.poll() is non-None.
        if os.name == "nt":
            handle = getattr(process, "_auren_job_handle", None)
            kernel32 = getattr(process, "_auren_kernel32", None)
            if handle and kernel32:
                try:
                    kernel32.TerminateJobObject(handle, 1)
                except OSError:
                    pass
                _close_windows_job(process)
                return
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

    def __call__(self, prompt: str, timeout_seconds: int | float, *, cancel_event: threading.Event | None = None) -> str:
        if not isinstance(prompt, str):
            raise TypeError("provider prompt must be text")
        if cancel_event is not None and not isinstance(cancel_event, threading.Event):
            raise TypeError("cancel_event must be a threading.Event")
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("provider invocation cancelled before launch")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or not 0 < float(timeout_seconds) <= 86400
        ):
            raise ValueError("timeout_seconds must be finite and between 0 and 86400 seconds")
        payload = prompt.encode("utf-8")
        if len(payload) > self.max_prompt_bytes:
            raise ValueError("provider prompt exceeds configured byte limit")
        timeout = float(timeout_seconds)
        wall_now = time.time()
        deadline_epoch = wall_now + timeout
        inherited_deadline = self.env.get("AUREN_DEADLINE_EPOCH", os.environ.get("AUREN_DEADLINE_EPOCH"))
        if inherited_deadline is not None:
            try:
                inherited_value = float(inherited_deadline)
            except (TypeError, ValueError) as exc:
                raise ValueError("AUREN_DEADLINE_EPOCH must be a finite Unix timestamp") from exc
            if not math.isfinite(inherited_value):
                raise ValueError("AUREN_DEADLINE_EPOCH must be a finite Unix timestamp")
            if inherited_value <= wall_now:
                raise TimeoutError("inherited supervisor deadline expired before provider launch")
            deadline_epoch = min(deadline_epoch, inherited_value)
        timeout = deadline_epoch - wall_now
        if timeout <= 0:
            raise TimeoutError("inherited supervisor deadline expired before provider launch")
        child_env = dict(self.env)
        child_env["AUREN_DEADLINE_EPOCH"] = f"{deadline_epoch:.6f}"
        started = time.monotonic()
        kwargs: dict[str, object] = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True
            if self.max_memory_bytes is not None or self.max_processes is not None:
                import resource
                def apply_limits() -> None:
                    if self.max_memory_bytes is not None:
                        if not hasattr(resource, "RLIMIT_AS"):
                            raise RuntimeError("memory_limit_unavailable: RLIMIT_AS is not supported")
                        resource.setrlimit(resource.RLIMIT_AS, (self.max_memory_bytes, self.max_memory_bytes))
                    if self.max_processes is not None:
                        if not hasattr(resource, "RLIMIT_NPROC"):
                            raise RuntimeError("process_limit_unavailable: RLIMIT_NPROC is not supported")
                        resource.setrlimit(resource.RLIMIT_NPROC, (self.max_processes, self.max_processes))
                kwargs["preexec_fn"] = apply_limits
        elif os.name == "nt":
            # Keep the child suspended until it belongs to a kill-on-close Job
            # Object, so it cannot spawn an uncontained descendant before assignment.
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000004
        process = subprocess.Popen(
            self.command, cwd=str(self.cwd), env=child_env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            shell=False, bufsize=0, **kwargs,
        )
        _windows_job(process, max_memory_bytes=self.max_memory_bytes, max_processes=self.max_processes)
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
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                self._kill(process)
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self._kill(process)
                writer.join(timeout=0.2)
                reader.join(timeout=0.2)
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
                raise InterruptedError("provider subprocess was cancelled and terminated")
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                self._kill(process)
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self._kill(process)
                writer.join(timeout=0.2)
                reader.join(timeout=0.2)
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
                raise TimeoutError("provider subprocess exceeded its deadline and was terminated")
            try:
                process.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                continue
        writer.join(timeout=0.2)
        reader.join(timeout=max(0.0, timeout - (time.monotonic() - started)))
        if overflow.is_set():
            raise ValueError("provider response exceeds configured byte limit")
        if read_error:
            raise SubprocessProviderError("provider response stream failed") from read_error[0]
        if reader.is_alive():
            self._kill(process)
            raise TimeoutError("provider output stream did not close before its deadline")
        # A provider may return successfully while a child process remains alive.
        # Kill the process group (POSIX) or close the Job Object (Windows) before
        # returning control to a consequential caller.
        if os.name == "posix":
            self._kill(process)
        else:
            _close_windows_job(process)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if process.returncode != 0:
            raise SubprocessProviderError(f"provider subprocess exited with code {process.returncode}")
        try:
            return bytes(output).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("provider response must be UTF-8 text") from exc
