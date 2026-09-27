"""Optional Rust-kernel bridge.

The Python runtime remains authoritative for orchestration and policy. When the
native aer-core executable is installed, deterministic hot-path operations may
be delegated to it. If it is unavailable or fails, callers retain their
existing Python implementation.

The bridge uses JSON Lines so the native process can later be replaced by an
in-process binding without changing the Python-facing contract.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Mapping, Sequence

DEFAULT_TIMEOUT_SECONDS = 5.0


def executable() -> str | None:
    configured = os.environ.get("AER_CORE_BIN")
    if configured:
        return configured if shutil.which(configured) or os.path.isfile(configured) else None
    return shutil.which("aer-core")


def available() -> bool:
    return executable() is not None


def _request(payload: Mapping[str, Any], *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any] | None:
    binary = executable()
    if not binary:
        return None
    try:
        completed = subprocess.run(
            [binary],
            input=json.dumps(payload, separators=(",", ":")) + "\n",
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return result if isinstance(result, dict) and result.get("ok") is True else None


def sha256(value: str) -> str | None:
    result = _request({"op": "sha256", "value": value})
    if not result:
        return None
    value = result.get("value")
    return value.get("sha256") if isinstance(value, dict) else None


def route_resource(task_class: str, observations: Sequence[Mapping[str, Any]], lanes: Sequence[str]) -> str | None:
    result = _request({
        "op": "resource_route",
        "task_class": task_class,
        "observations": list(observations),
        "lanes": list(lanes),
    })
    if not result:
        return None
    value = result.get("value")
    return value.get("lane") if isinstance(value, dict) else None
