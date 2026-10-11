"""Subprocess boundary for the Independent Generalization Arena.

The evaluator keeps the sealed manifest and oracle in the parent process. The
policy subprocess receives exactly one JSON task object on stdin and must return
one JSON value on stdout. This is a process boundary, not an OS sandbox: callers
must use a separately controlled container/account or external sandbox for
untrusted code and genuinely independent campaigns.
"""
from __future__ import annotations

import math
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from typing import Any

from .independent_arena import ArenaError, NonceLedger, run_campaign


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
    policy_environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute each case in a fresh policy subprocess.

    The command is invoked without a shell. Only the task-facing JSON object is
    sent to stdin; stdout must contain exactly one JSON value. Environment
    inheritance is allowlisted, stderr and exception details are never placed
    in receipts, and each invocation has a bounded timeout.
    """
    if isinstance(policy_command, (str, bytes)) or not isinstance(policy_command, Sequence) or not policy_command:
        raise ArenaError("policy_command must be a non-empty argument sequence")
    if any(not isinstance(arg, str) or not arg for arg in policy_command):
        raise ArenaError("policy_command arguments must be non-empty strings")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ArenaError("timeout_seconds must be a finite positive number")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ArenaError("timeout_seconds must be a finite positive number")
    env = _policy_environment(policy_environment)

    with tempfile.TemporaryDirectory(prefix="auren-arena-policy-") as workdir:
        def policy(task: Mapping[str, str]) -> Any:
            completed = subprocess.run(
                list(policy_command),
                input=__import__("json").dumps(
                    dict(task), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
                ),
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=workdir,
                env=env,
                timeout=float(timeout_seconds),
                check=False,
                shell=False,
                close_fds=True,
            )
            if completed.returncode != 0:
                raise ArenaError("policy subprocess returned a non-zero exit status")
            import json
            try:
                return json.loads(completed.stdout)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ArenaError("policy subprocess did not return one valid JSON value") from exc

        return run_campaign(
            sealed,
            policy,
            policy_digest=policy_digest,
            run_nonce=run_nonce,
            nonce_ledger=nonce_ledger,
        )


__all__ = ["run_campaign_isolated"]
