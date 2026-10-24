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

# The harness can be launched directly from .ai-harness/runtime, where the
# repository root is not guaranteed to be on sys.path.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from portable.bounded_subprocess import run_bounded_command


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
        env["AER_SANDBOX_NETWORK"] = "disabled"  # legacy policy marker
    env["AUREN_SANDBOX"] = "enforced"
    env["AER_SANDBOX"] = "enforced"  # legacy policy marker
    return env


def _validate_resource_policy(policy: SandboxPolicy) -> None:
    if isinstance(policy.timeout_seconds, bool) or not isinstance(policy.timeout_seconds, int) or policy.timeout_seconds < 1:
        raise SandboxViolation("timeout_seconds must be a positive integer")
    if type(policy.max_output_chars) is not int or policy.max_output_chars < 1:
        raise SandboxViolation("max_output_chars must be a positive integer")
    if type(policy.max_memory_mb) is not int or policy.max_memory_mb < 16:
        raise SandboxViolation("max_memory_mb must be an integer of at least 16 MiB")
    if type(policy.max_processes) is not int or policy.max_processes < 1:
        raise SandboxViolation("max_processes must be a positive integer")

def run(command: list[str], *, workspace: Path, policy: SandboxPolicy | None = None) -> subprocess.CompletedProcess[str]:
    policy = policy or SandboxPolicy()
    _validate_resource_policy(policy)
    workspace = Path(workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    validate_command(command, workspace=workspace, policy=policy)
    result = run_bounded_command(
        command,
        cwd=workspace,
        env=_limited_env(policy),
        timeout_seconds=max(1, int(policy.timeout_seconds)),
        max_memory_bytes=policy.max_memory_mb * 1024 * 1024,
        max_output_bytes=policy.max_output_chars,
    )
    if result.status != "completed":
        if result.status == "timed_out":
            raise SandboxViolation(f"command timed out: {result.reason or 'deadline_exceeded'}")
        if result.status == "cancelled":
            raise SandboxViolation("command cancelled")
        raise SandboxViolation(f"command execution failed: {result.reason or result.status}")
    output = result.stdout + result.stderr
    return subprocess.CompletedProcess(list(command), int(result.returncode or 0), result.stdout, output)
