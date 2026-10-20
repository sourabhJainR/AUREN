"""Bounded, revision-bound evidence checks for AUREN engineering tasks.

This module is a verification primitive, not an execution sandbox. Callers must
supply trusted argv vectors and run checks in an appropriately isolated host.
A PASS means every requested command exited successfully against the expected
Git revision; it does not mean the change is defect-free or independently safe.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence


@dataclass(frozen=True)
class EvidenceCheck:
    name: str
    argv: tuple[str, ...]
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("check name is required")
        if not self.argv or any(not isinstance(arg, str) or not arg for arg in self.argv):
            raise ValueError("argv must be a non-empty sequence of non-empty strings")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    returncode: int | None
    duration_seconds: float
    stdout: str
    stderr: str


@dataclass(frozen=True)
class EvidenceReceipt:
    schema_version: str
    expected_revision: str
    observed_revision: str | None
    status: str
    checks: tuple[CheckResult, ...]
    digest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "expected_revision": self.expected_revision,
            "observed_revision": self.observed_revision,
            "status": self.status,
            "checks": [asdict(check) for check in self.checks],
            "digest": self.digest,
        }


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError("unable to resolve repository HEAD")
    return result.stdout.strip()


def _bounded(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def run_evidence_gate(
    project_root: str | Path,
    *,
    expected_revision: str,
    checks: Sequence[EvidenceCheck],
    total_timeout_seconds: float = 600.0,
    output_limit_chars: int = 4000,
    command_runner: Callable[..., object] = subprocess.run,
    revision_reader: Callable[[Path], str] = _git_head,
) -> EvidenceReceipt:
    """Run bounded argv checks and issue a digest-bound PASS/FAIL/STALE receipt.

    Commands are never executed through a shell. The host remains responsible
    for allow-listing commands, credentials, network access, and filesystem
    isolation. A changed HEAD, timeout, exception, or non-zero exit can never
    produce PASS. No automatic retry is performed here.
    """
    if not isinstance(expected_revision, str) or not expected_revision.strip():
        raise ValueError("expected_revision is required")
    if total_timeout_seconds <= 0 or output_limit_chars < 1:
        raise ValueError("timeout and output limits must be positive")
    if not checks:
        raise ValueError("at least one evidence check is required")
    names = [check.name for check in checks]
    if len(names) != len(set(names)):
        raise ValueError("check names must be unique")

    root = Path(project_root).resolve()
    try:
        observed = revision_reader(root)
    except Exception:
        observed = None
    if observed != expected_revision:
        status = "STALE"
        results: tuple[CheckResult, ...] = ()
    else:
        deadline = time.monotonic() + total_timeout_seconds
        accumulated: list[CheckResult] = []
        for check in checks:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                accumulated.append(CheckResult(check.name, "TIMEOUT", None, 0.0, "", "total evidence budget exhausted"))
                accumulated.extend(
                    CheckResult(pending.name, "NOT_RUN", None, 0.0, "", "prior check exhausted total evidence budget")
                    for pending in checks[len(accumulated):]
                )
                break
            timeout = min(check.timeout_seconds, remaining)
            started = time.monotonic()
            try:
                result = command_runner(
                    list(check.argv),
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                    shell=False,
                )
                returncode = int(getattr(result, "returncode"))
                stdout = _bounded(str(getattr(result, "stdout", "") or ""), output_limit_chars)
                stderr = _bounded(str(getattr(result, "stderr", "") or ""), output_limit_chars)
                check_status = "PASS" if returncode == 0 else "FAIL"
            except subprocess.TimeoutExpired as exc:
                returncode = None
                stdout = _bounded(str(exc.stdout or ""), output_limit_chars)
                stderr = _bounded(str(exc.stderr or "command timed out"), output_limit_chars)
                check_status = "TIMEOUT"
            except Exception as exc:
                returncode = None
                stdout = ""
                stderr = _bounded(f"{type(exc).__name__}: {exc}", output_limit_chars)
                check_status = "ERROR"
            accumulated.append(CheckResult(
                check.name, check_status, returncode,
                round(max(0.0, time.monotonic() - started), 6), stdout, stderr,
            ))
            if check_status != "PASS":
                accumulated.extend(
                    CheckResult(pending.name, "NOT_RUN", None, 0.0, "", "blocked by prior failed check")
                    for pending in checks[len(accumulated):]
                )
                break
        results = tuple(accumulated)
        status = "PASS" if all(result.status == "PASS" for result in results) and len(results) == len(checks) else "FAIL"

    payload = {
        "schema_version": "auren-evidence-receipt-v1",
        "expected_revision": expected_revision,
        "observed_revision": observed,
        "status": status,
        "checks": [asdict(check) for check in results],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return EvidenceReceipt(
        schema_version="auren-evidence-receipt-v1",
        expected_revision=expected_revision,
        observed_revision=observed,
        status=status,
        checks=results,
        digest=digest,
    )
