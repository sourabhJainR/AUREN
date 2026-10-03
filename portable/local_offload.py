"""Resource-aware local work offload for AUREN.

This is an additive execution layer. It does not replace the existing graph,
planner, memory, verification, or policy owners. It gives those layers a small
local worker pool for bounded, independent work such as tests, static analysis,
repository inspection, and other CPU-bound commands.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

_SECRET_ENV = re.compile(r"(?i)(token|secret|password|api[_-]?key|private[_-]?key|credential|auth)")
_BLOCKED_COMMANDS = {"rm", "rmdir", "del", "format", "shutdown", "reboot"}
_BLOCKED_GIT = {"push", "reset", "clean", "checkout", "switch", "merge", "rebase", "branch"}


def discover_resources() -> dict[str, int | str]:
    """Return conservative local capacity information."""
    cpu = max(1, int(os.cpu_count() or 1))
    memory_mb = 0
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            raw = meminfo.read_text(encoding="utf-8")
            match = re.search(r"^MemAvailable:\s+(\d+)\s+kB$", raw, re.MULTILINE)
            if match:
                memory_mb = max(1, int(match.group(1)) // 1024)
        except OSError:
            pass
    return {
        "cpu_count": cpu,
        "recommended_workers": max(1, min(cpu, cpu + 1)),
        "available_memory_mb": memory_mb,
        "platform": platform.system().lower(),
    }


@dataclass(frozen=True)
class ResourceBudget:
    max_workers: int = 0
    timeout_seconds: float = 300.0
    max_output_chars: int = 20_000
    memory_mb: int = 0

    def normalized(self) -> "ResourceBudget":
        resources = discover_resources()
        workers = self.max_workers or int(resources["recommended_workers"])
        workers = max(1, min(workers, int(resources["cpu_count"])))
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_output_chars < 100:
            raise ValueError("max_output_chars must be at least 100")
        if self.memory_mb < 0:
            raise ValueError("memory_mb cannot be negative")
        return ResourceBudget(
            max_workers=workers,
            timeout_seconds=float(self.timeout_seconds),
            max_output_chars=int(self.max_output_chars),
            memory_mb=int(self.memory_mb),
        )


@dataclass(frozen=True)
class OffloadJob:
    job_id: str
    command: tuple[str, ...]
    cwd: str | None = None
    timeout_seconds: float | None = None
    max_output_chars: int | None = None
    isolate: bool = False
    allow_write: bool = False


@dataclass(frozen=True)
class OffloadResult:
    job_id: str
    status: str
    exit_code: int | None
    duration_seconds: float
    output: str
    error: str | None = None
    workspace: str | None = None


class LocalOffloadBroker:
    """Run bounded independent local jobs without owning AUREN orchestration."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        budget: ResourceBudget | None = None,
        allowed_programs: Iterable[str] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        if not self.project_root.is_dir():
            raise ValueError("project_root must be an existing directory")
        self.budget = (budget or ResourceBudget()).normalized()
        self._state_lock = threading.Lock()
        self._active_jobs = 0
        self._queued_jobs = 0
        self._completed_jobs = 0
        self.allowed_programs = set(allowed_programs) if allowed_programs is not None else {
            "python", "python3", "pytest", "ruff", "mypy", "pyright", "git"
        }

    def capacity(self) -> dict[str, int | float | str]:
        resources = discover_resources()
        with self._state_lock:
            active, queued, completed = self._active_jobs, self._queued_jobs, self._completed_jobs
        return {
            **resources,
            "configured_workers": self.budget.max_workers,
            "timeout_seconds": self.budget.timeout_seconds,
            "max_output_chars": self.budget.max_output_chars,
            "memory_budget_mb": self.budget.memory_mb,
            "active_jobs": active,
            "queued_jobs": queued,
            "completed_jobs": completed,
            "queue_capacity": self.budget.max_workers * 8,
        }

    def pressure(self) -> dict[str, float | int]:
        """Return a cheap, bounded snapshot for resource-aware routing."""
        capacity = self.capacity()
        cpu = max(1, int(capacity["cpu_count"]))
        available_memory = int(capacity["available_memory_mb"])
        memory_budget = int(capacity["memory_budget_mb"])
        active = int(capacity["active_jobs"])
        queued = int(capacity["queued_jobs"])
        workers = max(1, int(capacity["configured_workers"]))
        memory_pressure = 0.0 if not memory_budget or not available_memory else min(1.0, memory_budget / available_memory)
        return {
            "cpu_pressure": min(1.0, active / cpu),
            "queue_pressure": min(1.0, (active + queued) / max(1, workers * 8)),
            "memory_pressure": memory_pressure,
        }

    def run(self, job: OffloadJob) -> OffloadResult:
        started = time.monotonic()
        with self._state_lock:
            self._queued_jobs += 1
        workspace: Path | None = None
        try:
            command = self._validate_command(job.command, allow_write=job.allow_write)
            timeout = float(job.timeout_seconds or self.budget.timeout_seconds)
            output_limit = int(job.max_output_chars or self.budget.max_output_chars)
            cwd = self._resolve_cwd(job.cwd)

            if job.isolate:
                workspace = Path(tempfile.mkdtemp(prefix="auren-offload-"))
                self._copy_workspace(self.project_root, workspace)
                cwd = self._map_isolated_cwd(self.project_root, cwd, workspace)

            with self._state_lock:
                self._queued_jobs -= 1
                self._active_jobs += 1
            process = subprocess.Popen(
                list(command),
                cwd=str(cwd),
                env=self._safe_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                **self._resource_limits(),
            )
            try:
                output, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate()
                return OffloadResult(
                    job.job_id, "timeout", None, time.monotonic() - started,
                    self._truncate(output, output_limit),
                    f"job exceeded {timeout:.1f}s timeout", str(cwd)
                )

            output = self._truncate(output, output_limit)
            status = "passed" if process.returncode == 0 else "failed"
            return OffloadResult(
                job.job_id, status, process.returncode, time.monotonic() - started,
                output, None if process.returncode == 0 else f"exit code {process.returncode}", str(cwd)
            )
        except Exception as exc:
            return OffloadResult(
                job.job_id, "rejected" if isinstance(exc, ValueError) else "error",
                None, time.monotonic() - started, "", str(exc),
                str(workspace) if workspace else None
            )
        finally:
            with self._state_lock:
                if self._active_jobs:
                    self._active_jobs -= 1
                self._completed_jobs += 1
            if workspace:
                shutil.rmtree(workspace, ignore_errors=True)

    def run_many(self, jobs: Sequence[OffloadJob]) -> list[OffloadResult]:
        if not jobs:
            return []
        if len(jobs) > self.budget.max_workers * 8:
            raise ValueError("offload batch exceeds bounded queue capacity")
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.budget.max_workers, thread_name_prefix="auren-offload"
        ) as pool:
            futures = [pool.submit(self.run, job) for job in jobs]
            return [future.result() for future in futures]

    def _resolve_cwd(self, requested: str | None) -> Path:
        candidate = (self.project_root / requested).resolve() if requested else self.project_root
        try:
            candidate.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError("job cwd must remain inside project_root") from exc
        if not candidate.is_dir():
            raise ValueError("job cwd must be an existing directory")
        return candidate

    def _validate_command(self, command: Sequence[str], *, allow_write: bool) -> tuple[str, ...]:
        if not command or not all(str(part).strip() for part in command):
            raise ValueError("command is required")
        normalized = tuple(str(part) for part in command)
        executable = Path(normalized[0]).name.lower()
        if executable not in self.allowed_programs:
            raise ValueError(f"program '{executable}' is not allowed")
        if allow_write:
            return normalized
        if executable in _BLOCKED_COMMANDS:
            raise ValueError(f"destructive program '{executable}' is blocked")
        if executable == "git" and len(normalized) > 1 and normalized[1].lower() in _BLOCKED_GIT:
            raise ValueError(f"mutating git operation '{normalized[1]}' is blocked")
        return normalized

    @staticmethod
    def _safe_environment() -> dict[str, str]:
        result: dict[str, str] = {}
        allowed = {"PATH", "PYTHONPATH", "VIRTUAL_ENV", "SystemRoot", "TEMP", "TMP", "HOME", "USERPROFILE"}
        for key, value in os.environ.items():
            if not _SECRET_ENV.search(key) and key in allowed:
                result[key] = value
        return result

    def _resource_limits(self) -> dict[str, object]:
        if os.name == "nt":
            return {}
        try:
            import resource
        except ImportError:
            return {}
        cpu_seconds = max(1, int(self.budget.timeout_seconds))
        return {"preexec_fn": lambda: self._set_posix_limits(resource, cpu_seconds)}

    def _set_posix_limits(self, resource_module, cpu_seconds: int) -> None:
        resource_module.setrlimit(resource_module.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        if self.budget.memory_mb and hasattr(resource_module, "RLIMIT_AS"):
            limit = self.budget.memory_mb * 1024 * 1024
            resource_module.setrlimit(resource_module.RLIMIT_AS, (limit, limit))

    @staticmethod
    def _truncate(output: str, limit: int) -> str:
        if len(output) <= limit:
            return output
        return output[:max(0, limit - 64)] + "\n...[output truncated by AUREN local offload]"

    @staticmethod
    def _copy_workspace(source: Path, target: Path) -> None:
        ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")
        for item in source.iterdir():
            destination = target / item.name
            if item.is_dir():
                shutil.copytree(item, destination, ignore=ignore)
            else:
                shutil.copy2(item, destination)

    @staticmethod
    def _map_isolated_cwd(project_root: Path, cwd: Path, workspace: Path) -> Path:
        return workspace / cwd.relative_to(project_root)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run bounded local AUREN offload jobs.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-output", type=int, default=20_000)
    parser.add_argument("--memory-mb", type=int, default=0)
    parser.add_argument("--isolate", action="store_true")
    parser.add_argument("--allow-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = tuple(args.command[1:] if args.command[:1] == ("--",) else args.command)
    broker = LocalOffloadBroker(
        args.project_root,
        budget=ResourceBudget(
            max_workers=args.workers,
            timeout_seconds=args.timeout,
            max_output_chars=args.max_output,
            memory_mb=args.memory_mb,
        ),
    )
    result = broker.run(OffloadJob("cli", command, isolate=args.isolate, allow_write=args.allow_write))
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0 if result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
