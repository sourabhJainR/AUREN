"""Fresh-worktree verification receipts for graph worker proposals.

This is repository-level independent verification, not an OS sandbox or an
external evaluator. It reuses AUREN's isolated-worktree lifecycle.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .graph_workspace_adoption import WorkspaceProposal
from .vibe_coding_host import IsolatedGitWorktree

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
_ALLOWED_PROGRAMS = frozenset({"python", "python3", "pytest", "ruff", "mypy", "pyright", "git"})
_SAFE_ENV = frozenset({"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "VIRTUAL_ENV"})


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args], input=input_bytes,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        shell=False, timeout=60,
    )
    if result.returncode:
        detail = (result.stderr or b"").decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"git {' '.join(args[:3])} failed: {detail}")
    return result.stdout


def _changed_paths(root: Path, base_revision: str) -> tuple[str, ...]:
    paths = {
        item.decode("utf-8", errors="strict")
        for item in _git(root, "diff", "--name-only", "-z", base_revision, "--").split(b"\x00")
        if item
    }
    paths.update(
        item.decode("utf-8", errors="strict")
        for item in _git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\x00")
        if item
    )
    return tuple(sorted(paths))


def _safe_relative_path(value: str) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return bool(path.parts) and not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts) and path.as_posix() == value


class IndependentGraphVerifier:
    """Run named checks in a fresh isolated worktree and bind their actual results."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        verifier_identity: str,
        trusted_verifier_ids: Sequence[str],
        worktree_root: str | Path | None = None,
        max_checks: int = 12,
        max_timeout_seconds: int = 300,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        if not self.project_root.is_dir():
            raise ValueError("project_root must be an existing directory")
        if not isinstance(verifier_identity, str) or not verifier_identity.strip():
            raise ValueError("verifier_identity is required")
        trusted = {item.strip() for item in trusted_verifier_ids if isinstance(item, str) and item.strip()}
        if verifier_identity not in trusted:
            raise PermissionError("verifier identity is not in the trusted verifier allowlist")
        if type(max_checks) is not int or not 1 <= max_checks <= 64:
            raise ValueError("max_checks must be between 1 and 64")
        if type(max_timeout_seconds) is not int or not 1 <= max_timeout_seconds <= 1800:
            raise ValueError("max_timeout_seconds must be between 1 and 1800")
        self.verifier_identity = verifier_identity.strip()
        self.trusted_verifier_ids = frozenset(trusted)
        self.worktree_root = Path(worktree_root or (Path(tempfile.gettempdir()) / "auren-graph-verification")).expanduser().resolve()
        self.worktree_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.worktree_root.is_symlink():
            raise PermissionError("verification worktree root must not be a symlink")
        self.max_checks = max_checks
        self.max_timeout_seconds = max_timeout_seconds

    def verify(
        self,
        proposal: WorkspaceProposal,
        *,
        worker_identity: str,
        task_digest: str,
        intent_digest: str,
        checks: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(proposal, WorkspaceProposal):
            raise TypeError("verification requires a coordinator-issued WorkspaceProposal")
        if not isinstance(worker_identity, str) or not worker_identity.strip():
            raise ValueError("worker_identity is required")
        if worker_identity == self.verifier_identity:
            raise PermissionError("worker cannot verify its own proposal")
        if proposal.worker_id != worker_identity:
            raise PermissionError("worker identity does not match proposal owner")
        if not _SHA256.fullmatch(task_digest or "") or not _SHA256.fullmatch(intent_digest or ""):
            raise ValueError("task_digest and intent_digest must be lowercase SHA-256 digests")
        if not _SOURCE_REVISION.fullmatch(proposal.base_revision or ""):
            raise ValueError("proposal source revision is invalid")
        if not _SHA256.fullmatch(proposal.artifact_digest or ""):
            raise ValueError("proposal artifact digest is invalid")
        if not checks or len(checks) > self.max_checks:
            raise ValueError("one or more bounded independent verification checks are required")
        normalized_checks = []
        seen_ids: set[str] = set()
        for check in checks:
            if not isinstance(check, Mapping):
                raise TypeError("each verification check must be a mapping")
            check_id, argv = check.get("check_id"), check.get("argv")
            timeout = check.get("timeout_seconds", min(120, self.max_timeout_seconds))
            if not isinstance(check_id, str) or not check_id.strip() or check_id in seen_ids:
                raise ValueError("verification check ids must be unique non-empty strings")
            if not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(arg, str) or not arg for arg in argv):
                raise ValueError(f"check {check_id} must supply an explicit argv array")
            program = Path(argv[0]).name.lower()
            if program not in _ALLOWED_PROGRAMS:
                raise PermissionError(f"verification program is not allowed: {program}")
            if type(timeout) is not int or not 1 <= timeout <= self.max_timeout_seconds:
                raise ValueError(f"check {check_id} timeout is outside the configured limit")
            normalized_checks.append({"check_id": check_id.strip(), "argv": list(argv), "timeout_seconds": timeout})
            seen_ids.add(check_id)

        artifact_path = Path(proposal.artifact_path)
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise PermissionError("proposal artifact must be a regular file")
        patch = artifact_path.read_bytes()
        if hashlib.sha256(patch).hexdigest() != proposal.artifact_digest or patch != proposal.patch:
            raise ValueError("proposal artifact digest mismatch")
        if not proposal.paths or any(not _safe_relative_path(path) for path in proposal.paths):
            raise ValueError("proposal contains an unsafe or empty path set")
        if len(set(proposal.paths)) != len(proposal.paths):
            raise ValueError("proposal path set contains duplicates")

        current_revision = _git(self.project_root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
        if current_revision != proposal.base_revision:
            raise RuntimeError("stale source revision; refusing independent verification")
        if _git(self.project_root, "status", "--porcelain", "--untracked-files=all").strip():
            raise PermissionError("source checkout must be clean before independent verification")

        run_id = "verify-" + uuid.uuid4().hex
        run_root = Path(tempfile.mkdtemp(prefix="auren-verify-root-", dir=str(self.worktree_root))).resolve()
        lease = IsolatedGitWorktree(self.project_root, run_id, run_root)
        workspace: Path | None = None
        check_results: list[dict[str, Any]] = []
        cleanup_status = "not_started"
        try:
            workspace = lease.open()
            if lease.base_revision != proposal.base_revision:
                raise RuntimeError("fresh verifier worktree base does not match proposal revision")
            _git(workspace, "apply", "--check", "--", input_bytes=patch)
            _git(workspace, "apply", "--", input_bytes=patch)
            if _changed_paths(workspace, proposal.base_revision) != tuple(sorted(proposal.paths)):
                raise PermissionError("verification worktree artifact paths differ from proposal")
            env = {key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENV}
            env["PYTHONUTF8"] = "1"
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            for check in normalized_checks:
                started = time.monotonic()
                try:
                    completed = subprocess.run(
                        check["argv"], cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                        timeout=check["timeout_seconds"], check=False,
                    )
                    stdout = completed.stdout or b""
                    stderr = completed.stderr or b""
                    return_code = completed.returncode
                    status = "passed" if return_code == 0 else "failed"
                except subprocess.TimeoutExpired as exc:
                    stdout = exc.stdout or b""
                    stderr = exc.stderr or b""
                    return_code = None
                    status = "timed_out"
                except OSError as exc:
                    stdout = b""
                    stderr = str(exc).encode("utf-8", errors="replace")
                    return_code = None
                    status = "failed"
                output_digest = hashlib.sha256(stdout + b"\x00" + stderr).hexdigest()
                check_results.append({
                    "check_id": check["check_id"], "argv": check["argv"],
                    "status": status, "return_code": return_code,
                    "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                    "output_digest": output_digest,
                    "stdout_tail": stdout[-2000:].decode("utf-8", errors="replace"),
                    "stderr_tail": stderr[-2000:].decode("utf-8", errors="replace"),
                })
            status = "passed" if all(item["status"] == "passed" for item in check_results) else "failed"
        finally:
            if workspace is not None and workspace.exists():
                try:
                    lease.rollback(authorized=True)
                    _git(self.project_root, "worktree", "remove", "--force", str(workspace))
                    cleanup_status = "passed"
                except Exception:
                    cleanup_status = "failed"
            else:
                cleanup_status = "passed" if workspace is None else "failed"
            shutil.rmtree(run_root, ignore_errors=True)

        if cleanup_status != "passed":
            status = "failed"
        payload = {
            "schema": "auren-independent-graph-verification-v1",
            "status": status,
            "verification_mode": "fresh-isolated-worktree",
            "external_evaluator_independence": "not_proven",
            "worker_identity": worker_identity,
            "verifier_identity": self.verifier_identity,
            "task_digest": task_digest,
            "intent_digest": intent_digest,
            "source_revision": proposal.base_revision,
            "artifact_digest": proposal.artifact_digest,
            "verified_paths": list(sorted(proposal.paths)),
            "checks": check_results,
            "cleanup_status": cleanup_status,
        }
        payload["receipt_digest"] = _digest(payload)
        return payload


__all__ = ["IndependentGraphVerifier"]
