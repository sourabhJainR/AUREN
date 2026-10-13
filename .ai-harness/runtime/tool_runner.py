#!/usr/bin/env python3
"""AUREN command runner: one execution boundary for local tools and tests."""
from __future__ import annotations

import argparse
from pathlib import Path

try:
    from runtime.sandbox import SandboxPolicy, SandboxViolation, run
except ModuleNotFoundError:  # Direct script execution from .ai-harness/runtime/
    from sandbox import SandboxPolicy, SandboxViolation, run


def main() -> int:
    parser = argparse.ArgumentParser(description="AUREN sandboxed command runner")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    try:
        result = run(command, workspace=Path(args.workspace), policy=SandboxPolicy(timeout_seconds=args.timeout))
    except SandboxViolation as exc:
        print(f"AUREN SANDBOX: {exc}")
        return 78
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
