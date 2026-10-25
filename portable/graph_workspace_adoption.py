"""Conflict-safe graph worker workspaces and serialized proposal adoption.

This coordinator reuses AUREN's existing IsolatedGitWorktree. It does not own
task planning, policy, evidence, or execution budgets.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from .vibe_coding_host import IsolatedGitWorktree

_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def _git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, shell=False, timeout=60,
    )
    if result.returncode:
        detail = (result.stderr or b"").decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"git {' '.join(args[:3])} failed: {detail}")
    return result.stdout


def _clean_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError("owned paths must be non-empty repository-relative POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe owned path: {value!r}")
    normalized = path.as_posix()
    if normalized != value.rstrip("/"):
        raise ValueError(f"owned path must be normalized: {value!r}")
    return normalized


def _overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right.rstrip("/") + "/") or right.startswith(left.rstrip("/") + "/")


@dataclass(frozen=True)
class WorkspaceProposal:
    worker_id: str
    base_revision: str
    paths: tuple[str, ...]
    patch: bytes
    artifact_digest: str
    artifact_path: str


class GraphWorkspaceCoordinator:
    """Own isolated worker leases and serialize adoption into a clean checkout."""

    def __init__(self, project_root: str | Path, *, workspace_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        top = _git(self.project_root, "rev-parse", "--show-toplevel").decode().strip()
        if Path(top).resolve() != self.project_root:
            raise ValueError("project_root must be the top-level Git checkout")
        if _git(self.project_root, "status", "--porcelain", "--untracked-files=all").strip():
            raise PermissionError("source checkout must be clean before graph workspaces are allocated")
        self.base_revision = _git(self.project_root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
        self.workspace_root = Path(workspace_root or (Path(tempfile.gettempdir()) / "auren-graph-workspaces")).expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.artifact_root = self.workspace_root / ".proposals"
        self.artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.workspace_root.is_symlink() or self.artifact_root.is_symlink():
            raise PermissionError("workspace and proposal roots must not be symlinks")
        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        self._cancel_reason: str | None = None
        self._owners: dict[str, tuple[str, ...]] = {}
        self._leases: dict[str, IsolatedGitWorktree] = {}
        self._states: dict[str, str] = {}
        self._proposals: dict[str, WorkspaceProposal] = {}

    @property
    def cancellation_event(self) -> threading.Event:
        return self._cancel_event

    def create_worker(self, worker_id: str, owned_paths: Iterable[str]) -> Path:
        if not isinstance(worker_id, str) or not _WORKER_ID.fullmatch(worker_id):
            raise ValueError("worker_id must be a safe identifier")
        paths = tuple(sorted({_clean_path(path) for path in owned_paths}))
        if not paths:
            raise ValueError("each worker must declare at least one owned path")
        with self._lock:
            if self._cancel_event.is_set():
                raise RuntimeError(self._cancel_reason or "sibling cancellation requested")
            if worker_id in self._leases:
                raise ValueError("worker_id already exists")
            for other_id, other_paths in self._owners.items():
                if any(_overlap(left, right) for left in paths for right in other_paths):
                    raise PermissionError(f"file ownership overlaps worker {other_id}")
            lease = IsolatedGitWorktree(
                self.project_root, "graph-" + worker_id,
                self.workspace_root / "workers",
            )
            path = lease.open()
            if lease.base_revision != self.base_revision:
                raise RuntimeError("worker worktree base differs from coordinator base")
            self._owners[worker_id] = paths
            self._leases[worker_id] = lease
            self._states[worker_id] = "running"
            return path

    def prepare_proposal(self, worker_id: str) -> WorkspaceProposal:
        with self._lock:
            if worker_id not in self._leases:
                raise KeyError(f"unknown worker: {worker_id}")
            if self._states[worker_id] not in {"running", "cancel_requested"}:
                raise RuntimeError(f"worker cannot submit a proposal from state {self._states[worker_id]}")
            lease = self._leases[worker_id]
            workspace = lease.path
            if _git(workspace, "rev-parse", "HEAD").decode().strip() != self.base_revision:
                raise RuntimeError("worker base revision changed; proposal is stale")
            untracked = [item.decode("utf-8", errors="strict") for item in _git(
                workspace, "ls-files", "--others", "--exclude-standard", "-z"
            ).split(b"\x00") if item]
            if untracked:
                _git(workspace, "add", "-N", "--", *untracked)
            changed = tuple(sorted(
                item.decode("utf-8", errors="strict") for item in _git(
                    workspace, "diff", "--name-only", "-z", self.base_revision, "--"
                ).split(b"\x00") if item
            ))
            if not changed:
                raise ValueError("worker proposal has no changes")
            owned = self._owners[worker_id]
            outside = [path for path in changed if not any(path == root or path.startswith(root.rstrip("/") + "/") for root in owned)]
            if outside:
                raise PermissionError("worker changed paths outside declared ownership: " + ", ".join(outside))
            patch = _git(workspace, "diff", "--binary", self.base_revision, "--")
            digest = hashlib.sha256(patch).hexdigest()
            artifact = self.artifact_root / f"{worker_id}-{digest}.patch"
            if artifact.is_symlink():
                raise PermissionError("proposal artifact path must not be a symlink")
            artifact.write_bytes(patch)
            os.chmod(artifact, 0o600)
            metadata = {
                "worker_id": worker_id, "base_revision": self.base_revision,
                "paths": list(changed), "artifact_digest": digest,
            }
            meta_path = artifact.with_suffix(".json")
            meta_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            os.chmod(meta_path, 0o600)
            proposal = WorkspaceProposal(worker_id, self.base_revision, changed, patch, digest, str(artifact))
            self._proposals[worker_id] = proposal
            self._states[worker_id] = "proposal_ready"
            return proposal

    def adopt(self, proposal: WorkspaceProposal) -> tuple[str, ...]:
        """Apply one proposal only after fresh base, digest, scope, and conflict checks."""
        with self._lock:
            if self._cancel_event.is_set():
                raise RuntimeError(self._cancel_reason or "adoption blocked by cancellation")
            known = self._proposals.get(proposal.worker_id)
            if known != proposal:
                raise PermissionError("proposal is not the coordinator's verified artifact")
            if hashlib.sha256(proposal.patch).hexdigest() != proposal.artifact_digest:
                raise ValueError("proposal artifact digest mismatch")
            artifact = Path(proposal.artifact_path)
            if artifact.is_symlink() or not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != proposal.artifact_digest:
                raise ValueError("persisted proposal artifact digest mismatch")
            if proposal.base_revision != self.base_revision:
                raise RuntimeError("stale proposal base revision")
            current = _git(self.project_root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
            if current != proposal.base_revision:
                raise RuntimeError("stale target HEAD; refusing adoption")
            if _git(self.project_root, "status", "--porcelain", "--untracked-files=all").strip():
                raise PermissionError("target checkout is not clean; refusing overlapping or uncommitted adoption")
            owned = self._owners[proposal.worker_id]
            if any(not any(path == root or path.startswith(root.rstrip("/") + "/") for root in owned) for path in proposal.paths):
                raise PermissionError("proposal exceeds declared file ownership")
            _git(self.project_root, "apply", "--check", "--", input_bytes=proposal.patch)
            _git(self.project_root, "apply", "--", input_bytes=proposal.patch)
            adopted_paths = {
                item.decode("utf-8", errors="strict") for item in _git(
                    self.project_root, "diff", "--name-only", "-z", proposal.base_revision, "--"
                ).split(b"\x00") if item
            }
            adopted_paths.update(
                item.decode("utf-8", errors="strict") for item in _git(
                    self.project_root, "ls-files", "--others", "--exclude-standard", "-z"
                ).split(b"\x00") if item
            )
            adopted = tuple(sorted(adopted_paths))
            if adopted != proposal.paths:
                raise RuntimeError("adopted path set differs from verified proposal")
            self._states[proposal.worker_id] = "adopted"
            return adopted

    def cancel_siblings(self, reason: str, *, except_worker_id: str | None = None) -> dict[str, str]:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("cancellation reason is required")
        with self._lock:
            if self._cancel_reason is None:
                self._cancel_reason = reason.strip()[:500]
            self._cancel_event.set()
            requested = {}
            for worker_id, state in self._states.items():
                if worker_id == except_worker_id or state in {"adopted", "cleaned", "stopped"}:
                    continue
                self._states[worker_id] = "cancel_requested"
                requested[worker_id] = "cancel_requested"
            return requested

    def mark_worker_stopped(self, worker_id: str) -> None:
        with self._lock:
            if worker_id not in self._states:
                raise KeyError(f"unknown worker: {worker_id}")
            if self._states[worker_id] != "cancel_requested":
                raise RuntimeError("worker stop may only be recorded after cancellation was requested")
            self._states[worker_id] = "stopped"

    def cleanup_worker(self, worker_id: str) -> str:
        """Remove only a confirmed-stopped or completed isolated worktree."""
        with self._lock:
            if worker_id not in self._leases:
                raise KeyError(f"unknown worker: {worker_id}")
            state = self._states[worker_id]
            if state not in {"stopped", "proposal_ready", "adopted"}:
                raise RuntimeError("refusing to clean a worker that may still be running")
            lease = self._leases[worker_id]
            status = _git(lease.path, "status", "--porcelain", "--untracked-files=all").strip()
            if status and worker_id not in self._proposals:
                raise PermissionError("dirty worker has no persisted proposal artifact; refusing destructive cleanup")
            _git(self.project_root, "worktree", "remove", "--force", str(lease.path))
            self._states[worker_id] = "cleaned"
            return str(lease.path)

    def worker_states(self) -> dict[str, str]:
        with self._lock:
            return dict(sorted(self._states.items()))


__all__ = ["GraphWorkspaceCoordinator", "WorkspaceProposal"]
