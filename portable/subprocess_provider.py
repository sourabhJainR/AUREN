"""Killable subprocess boundary for command-backed providers.

No shell is used. The child does not inherit the host environment. Output is
read incrementally with a strict byte cap. POSIX invocations use a process group;
Windows invocations use a kill-on-close Job Object. Descendants are terminated
on cancellation, timeout, output failure, and normal provider exit.
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




def _windows_job(process: subprocess.Popen[bytes]):
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
    try:
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits),
        ):
            raise OSError(ctypes.get_last_error(), "could not configure provider Job Object")
        if not kernel32.AssignProcessToJobObject(handle, process._handle):
            raise OSError(ctypes.get_last_error(), "could not assign provider to Job Object")
        if kernel32.ResumeThread(process._thread) == 0xFFFFFFFF:
            raise OSError(ctypes.get_last_error(), "could not resume provider process")
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
            # Keep the child suspended until it belongs to a kill-on-close Job
            # Object, so it cannot spawn an uncontained descendant before assignment.
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000004
        process = subprocess.Popen(
            self.command, cwd=str(self.cwd), env=dict(self.env),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            shell=False, bufsize=0, **kwargs,
        )
        _windows_job(process)
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
        if process.returncode != 0:
            raise SubprocessProviderError(f"provider subprocess exited with code {process.returncode}")
        try:
            return bytes(output).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("provider response must be UTF-8 text") from exc
