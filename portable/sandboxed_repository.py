"""Sandboxed repository execution for bounded engineering episodes."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Sequence


@dataclass(frozen=True)
class CommandSpec:
    name: str
    argv: tuple[str, ...]
    timeout_seconds: int = 120


@dataclass(frozen=True)
class CommandEvidence:
    name: str
    argv: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    evidence_id: str


@dataclass(frozen=True)
class RepositoryInspection:
    root: str
    files: tuple[str, ...]
    git_head: str
    evidence_id: str


@dataclass(frozen=True)
class RepositoryExecutionResult:
    workspace: str
    changed_files: tuple[str, ...]
    commands: tuple[CommandEvidence, ...]
    passed: bool
    evidence_ids: tuple[str, ...]
    failure: str = ""


class SandboxedRepository:
    """Execute explicitly allow-listed commands in an isolated workspace.

    This class never mutates the source repository. The caller must provide a
    source directory and explicit commands; shell=True is deliberately absent.
    """

    def __init__(
        self,
        source: Path | str,
        *,
        allowed_commands: Sequence[str] = ("python", "pytest", "git"),
        max_output_chars: int = 20_000,
    ) -> None:
        self.source = Path(source).resolve()
        self.allowed_commands = frozenset(allowed_commands)
        self.max_output_chars = max_output_chars
        if not self.source.is_dir():
            raise ValueError("source must be a directory")

    def inspect(self) -> RepositoryInspection:
        files = tuple(
            sorted(
                str(p.relative_to(self.source))
                for p in self.source.rglob("*")
                if p.is_file() and ".git" not in p.parts
            )
        )
        try:
            head = self._run_readonly(("git", "rev-parse", "HEAD"), self.source)[0].strip()
        except RuntimeError:
            head = "unversioned:" + self._digest("snapshot", *files)
        evidence_id = self._digest("inspection", head, *files)
        return RepositoryInspection(str(self.source), files, head, evidence_id)

    def execute(
        self,
        commands: Sequence[CommandSpec],
        *,
        timeout_seconds: int | None = None,
    ) -> RepositoryExecutionResult:
        if not commands:
            raise ValueError("at least one command is required")

        with tempfile.TemporaryDirectory(prefix="aer-engineering-") as temp:
            workspace = Path(temp) / self.source.name
            shutil.copytree(
                self.source,
                workspace,
                ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
            )
            before = self._snapshot(workspace)
            evidence: list[CommandEvidence] = []
            passed = True
            failure = ""

            for spec in commands:
                self._validate_command(spec)
                result = self._execute_one(workspace, spec, timeout_seconds)
                evidence.append(result)
                if result.return_code != 0:
                    passed = False
                    failure = spec.name
                    break

            after = self._snapshot(workspace)
            changed = tuple(sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k)))
            ids = tuple(x.evidence_id for x in evidence)
            return RepositoryExecutionResult(
                str(workspace),
                changed,
                tuple(evidence),
                passed,
                ids,
                failure,
            )

    def _validate_command(self, spec: CommandSpec) -> None:
        if not spec.name.strip() or not spec.argv:
            raise ValueError("command must have a name and argv")
        executable = Path(spec.argv[0]).name
        if executable not in self.allowed_commands:
            raise PermissionError(f"command not allow-listed: {executable}")
        if any(not isinstance(x, str) or not x for x in spec.argv):
            raise ValueError("command arguments must be non-empty strings")
        if spec.timeout_seconds <= 0 or spec.timeout_seconds > 900:
            raise ValueError("command timeout must be between 1 and 900 seconds")

    def _execute_one(
        self,
        workspace: Path,
        spec: CommandSpec,
        override_timeout: int | None,
    ) -> CommandEvidence:
        import time
        start = time.monotonic()
        try:
            completed = subprocess.run(
                list(spec.argv),
                cwd=workspace,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=override_timeout or spec.timeout_seconds,
                check=False,
                shell=False,
                env=self._environment(),
            )
            code = completed.returncode
            stdout = completed.stdout[-self.max_output_chars:]
            stderr = completed.stderr[-self.max_output_chars:]
        except subprocess.TimeoutExpired as exc:
            code = 124
            stdout = str(exc.stdout or "")[-self.max_output_chars:]
            stderr = str(exc.stderr or "")[-self.max_output_chars:]
        duration = time.monotonic() - start
        evidence_id = self._digest(
            spec.name, " ".join(spec.argv), str(code), stdout, stderr, f"{duration:.3f}"
        )
        return CommandEvidence(spec.name, spec.argv, code, stdout, stderr, duration, evidence_id)

    @staticmethod
    def _snapshot(root: Path) -> dict[str, str]:
        result: dict[str, str] = {}
        for p in root.rglob("*"):
            if p.is_file() and ".git" not in p.parts:
                result[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
        return result

    @staticmethod
    def _digest(*values: str) -> str:
        payload = "\0".join(values).encode()
        return "repo:" + hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _environment() -> dict[str, str]:
        keep = ("PATH", "PYTHONPATH", "LANG", "LC_ALL", "HOME", "TMPDIR")
        return {k: v for k, v in os.environ.items() if k in keep}
    
    @staticmethod
    def _run_readonly(argv: tuple[str, ...], cwd: Path) -> tuple[str, str]:
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            shell=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "inspection command failed")
        return result.stdout, result.stderr


__all__ = [
    "CommandSpec",
    "CommandEvidence",
    "RepositoryInspection",
    "RepositoryExecutionResult",
    "SandboxedRepository",
]
