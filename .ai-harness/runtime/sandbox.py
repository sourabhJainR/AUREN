#!/usr/bin/env python3
"""Dependency-free execution sandbox for local engineering commands.

The sandbox is intentionally capability based: no shell, bounded runtime,
workspace confinement, filtered environment and optional OS resource limits.
It is not a replacement for a VM/container; callers should use a stronger OS
sandbox for hostile code.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore[assignment]


@dataclass(frozen=True)
class SandboxPolicy:
    enabled: bool = True
    timeout_seconds: int = 600
    max_output_chars: int = 12000
    max_memory_mb: int = 2048
    max_processes: int = 32
    allow_network: bool = False
    env_allowlist: tuple[str, ...] = (
        "PATH", "HOME", "USER", "LANG", "LC_ALL", "TEMP", "TMP", "TMPDIR",
        "PYTHONPATH", "VIRTUAL_ENV", "CI", "TERM", "SystemRoot", "ComSpec",
    )
    denied_patterns: tuple[str, ...] = (
        r"(^|/)(rm|rmdir|del)(\s|$)",
        r"(^|/)(shutdown|reboot)(\s|$)",
        r"(^|/)mkfs(\.|\s|$)",
        r"(^|/)dd(\s|$)",
    )
    extra_env: dict[str, str] = field(default_factory=dict)


class SandboxViolation(RuntimeError):
    pass


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate_command(command: list[str], *, workspace: Path, policy: SandboxPolicy) -> None:
    if not policy.enabled:
        return
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise SandboxViolation("empty or invalid command")
    rendered = " ".join(command)
    for pattern in policy.denied_patterns:
        if re.search(pattern, rendered, re.IGNORECASE):
            raise SandboxViolation(f"command denied by sandbox policy: {command[0]}")
    if not _inside(workspace, workspace):
        raise SandboxViolation("invalid workspace")


def _limited_env(policy: SandboxPolicy) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in policy.env_allowlist}
    env.update(policy.extra_env)
    if not policy.allow_network:
        env["AUREN_SANDBOX_NETWORK"] = "disabled"
    env["AUREN_SANDBOX"] = "enforced"
    return env


def _resource_limits(policy: SandboxPolicy):
    if resource is None:
        return None

    def apply() -> None:
        try:
            cpu = max(1, int(policy.timeout_seconds))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 2))
            memory = max(128, int(policy.max_memory_mb)) * 1024 * 1024
            if hasattr(resource, "RLIMIT_AS"):
                resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            if hasattr(resource, "RLIMIT_NPROC"):
                resource.setrlimit(resource.RLIMIT_NPROC, (max(1, int(policy.max_processes)), max(1, int(policy.max_processes))))
        except (OSError, ValueError):
            pass

    return apply


def run(command: list[str], *, workspace: Path, policy: SandboxPolicy | None = None) -> subprocess.CompletedProcess[str]:
    policy = policy or SandboxPolicy()
    workspace = Path(workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    validate_command(command, workspace=workspace, policy=policy)
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=_limited_env(policy),
            shell=False,
            text=True,
            capture_output=True,
            timeout=max(1, int(policy.timeout_seconds)),
            preexec_fn=_resource_limits(policy) if sys.platform != "win32" else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxViolation(f"command timed out after {policy.timeout_seconds}s") from exc
    output = ((completed.stdout or "") + (completed.stderr or ""))[-policy.max_output_chars :]
    return subprocess.CompletedProcess(completed.args, completed.returncode, completed.stdout, output)
