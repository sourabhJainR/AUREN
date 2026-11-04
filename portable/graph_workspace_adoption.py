"""Conflict-safe graph worker workspaces and serialized proposal adoption.

This coordinator reuses AUREN's existing IsolatedGitWorktree. It does not own
task planning, policy, evidence, or execution budgets.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import threading
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


from .vibe_coding_host import IsolatedGitWorktree


class _RecoveredLease:
    def __init__(self, path: Path, base_revision: str) -> None:
        self.path = path
        self.base_revision = base_revision

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


def _path_fingerprint(root: Path, relative_path: str) -> str:
    target = root.joinpath(*PurePosixPath(_clean_path(relative_path)).parts)
    if target.is_symlink():
        return hashlib.sha256(b"symlink" + bytes([0]) + os.readlink(target).encode("utf-8", errors="surrogateescape")).hexdigest()
    if not target.exists():
        return "missing"
    if not target.is_file():
        raise RuntimeError("adoption recovery encountered a non-file proposal path")
    return hashlib.sha256(b"file" + bytes([0]) + target.read_bytes()).hexdigest()


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

    def __init__(self, project_root: str | Path, *, workspace_root: str | Path | None = None, registry_path: str | Path | None = None) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        top = _git(self.project_root, "rev-parse", "--show-toplevel").decode().strip()
        if Path(top).resolve() != self.project_root:
            raise ValueError("project_root must be the top-level Git checkout")
        self.base_revision = _git(self.project_root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
        self.workspace_root = Path(workspace_root or (Path(tempfile.gettempdir()) / "auren-graph-workspaces")).expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.artifact_root = self.workspace_root / ".proposals"
        self.artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.adoption_root = self.workspace_root / ".adoptions"
        self.adoption_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.workspace_root.is_symlink() or self.artifact_root.is_symlink() or self.adoption_root.is_symlink():
            raise PermissionError("workspace and proposal roots must not be symlinks")
        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        self._cancel_reason: str | None = None
        self._owners: dict[str, tuple[str, ...]] = {}
        self._leases: dict[str, IsolatedGitWorktree] = {}
        self._states: dict[str, str] = {}
        self._proposals: dict[str, WorkspaceProposal] = {}
        self._captured_artifacts: dict[str, str] = {}
        self._termination_evidence: dict[str, str] = {}
        registry_candidate = Path(registry_path or (self.workspace_root / "workspace-registry.sqlite3")).expanduser()
        if registry_candidate.is_symlink():
            raise PermissionError("workspace registry must not be a symlink")
        self.registry_path = registry_candidate.resolve()
        self.registry_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._registry = sqlite3.connect(self.registry_path, timeout=10, check_same_thread=False)
        os.chmod(self.registry_path, 0o600)
        self._registry.execute("PRAGMA journal_mode=WAL")
        self._registry.execute("PRAGMA synchronous=FULL")
        self._registry.execute("""CREATE TABLE IF NOT EXISTS graph_workspace_registry (
            worker_id TEXT PRIMARY KEY, base_revision TEXT NOT NULL, owned_paths TEXT NOT NULL,
            workspace_path TEXT NOT NULL, state TEXT NOT NULL, artifact_path TEXT,
            artifact_digest TEXT, proposal_paths TEXT, cancellation_reason TEXT,
            termination_evidence TEXT
        )""")
        columns = {row[1] for row in self._registry.execute("PRAGMA table_info(graph_workspace_registry)")}
        if "termination_evidence" not in columns:
            self._registry.execute("ALTER TABLE graph_workspace_registry ADD COLUMN termination_evidence TEXT")
        self._registry.execute("""CREATE TABLE IF NOT EXISTS graph_workspace_adoptions (
            adoption_id TEXT PRIMARY KEY, base_revision TEXT NOT NULL, worker_ids TEXT NOT NULL,
            paths TEXT NOT NULL, artifact_path TEXT NOT NULL, artifact_digest TEXT NOT NULL,
            state TEXT NOT NULL, result_manifest TEXT
        )""")
        adoption_columns = {row[1] for row in self._registry.execute("PRAGMA table_info(graph_workspace_adoptions)")}
        if "result_manifest" not in adoption_columns:
            self._registry.execute("ALTER TABLE graph_workspace_adoptions ADD COLUMN result_manifest TEXT")
        self._registry.execute("""CREATE TABLE IF NOT EXISTS graph_workspace_adoption_commits (
            adoption_id TEXT PRIMARY KEY, base_revision TEXT NOT NULL, commit_sha TEXT NOT NULL,
            paths TEXT NOT NULL, result_manifest TEXT NOT NULL, receipt_digest TEXT NOT NULL
        )""")
        self._registry.commit()
        self._committed_adoptions: dict[str, dict] = {}
        self._committed_workers: dict[str, str] = {}
        self._recovered_adoption_pending_commit = False
        try:
            self._load_commit_receipts()
            self._restore_registry()
            self._reconcile_adoption_journal()
            if _git(self.project_root, "status", "--porcelain", "--untracked-files=all").strip() and not self._recovered_adoption_pending_commit:
                raise PermissionError("source checkout must be clean before graph workspaces are allocated")
        except BaseException:
            self._registry.close()
            self._registry = None
            raise

    def _persist_registry(self) -> None:
        """Durably snapshot worker ownership and lifecycle before returning to callers."""
        with self._registry:
            for worker_id, state in self._states.items():
                lease = self._leases[worker_id]
                proposal = self._proposals.get(worker_id)
                artifact_path = proposal.artifact_path if proposal else self._captured_artifacts.get(worker_id)
                artifact_digest = proposal.artifact_digest if proposal else None
                proposal_paths = json.dumps(list(proposal.paths)) if proposal else None
                self._registry.execute(
                    """INSERT INTO graph_workspace_registry
                    (worker_id, base_revision, owned_paths, workspace_path, state, artifact_path,
                     artifact_digest, proposal_paths, cancellation_reason, termination_evidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(worker_id) DO UPDATE SET base_revision=excluded.base_revision,
                    owned_paths=excluded.owned_paths, workspace_path=excluded.workspace_path,
                    state=excluded.state, artifact_path=excluded.artifact_path,
                    artifact_digest=excluded.artifact_digest, proposal_paths=excluded.proposal_paths,
                    cancellation_reason=excluded.cancellation_reason,
                    termination_evidence=excluded.termination_evidence""",
                    (worker_id, lease.base_revision, json.dumps(list(self._owners[worker_id])),
                     str(lease.path), state, artifact_path, artifact_digest, proposal_paths,
                     self._cancel_reason, self._termination_evidence.get(worker_id)),
                )

    @staticmethod
    def _canonical_digest(payload: dict) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        ).hexdigest()

    def _persist_adoption_intent(self, worker_ids, paths, patch, result_manifest=None):
        digest = hashlib.sha256(patch).hexdigest()
        worker_ids = tuple(worker_ids)
        paths = tuple(sorted(set(paths)))
        if result_manifest is None:
            result_manifest = {}
            for worker_id in worker_ids:
                proposal = self._proposals.get(worker_id)
                lease = self._leases.get(worker_id)
                if proposal is None or lease is None:
                    raise RuntimeError("adoption intent requires persisted proposal and workspace evidence")
                for relative_path in proposal.paths:
                    if relative_path in result_manifest:
                        raise PermissionError("adoption proposals overlap a result path")
                    result_manifest[relative_path] = _path_fingerprint(lease.path, relative_path)
        result_manifest = dict(sorted(result_manifest.items()))
        if set(result_manifest) != set(paths):
            raise RuntimeError("adoption result manifest does not match the verified path set")
        manifest_json = json.dumps(result_manifest, sort_keys=True, separators=(",", ":"))
        adoption_id = hashlib.sha256((self.base_revision + digest + "|".join(worker_ids) + os.urandom(8).hex()).encode()).hexdigest()
        artifact = self.adoption_root / (adoption_id + ".patch")
        if artifact.is_symlink():
            raise PermissionError("adoption artifact path must not be a symlink")
        if artifact.exists() and hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
            raise RuntimeError("existing adoption artifact digest mismatch")
        if not artifact.exists():
            with artifact.open("xb") as stream:
                stream.write(patch)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(artifact, 0o600)
        with self._registry:
            self._registry.execute(
                """INSERT INTO graph_workspace_adoptions
                (adoption_id, base_revision, worker_ids, paths, artifact_path, artifact_digest, state, result_manifest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (adoption_id, self.base_revision, json.dumps(list(worker_ids)),
                 json.dumps(list(paths)), str(artifact), digest, "applying", manifest_json),
            )
        return adoption_id

    def _record_adoption_outcome(self, adoption_id: str, state: str) -> None:
        row = self._registry.execute(
            """SELECT base_revision, worker_ids, paths, artifact_path, artifact_digest, result_manifest
            FROM graph_workspace_adoptions WHERE adoption_id=? AND state='applying'""",
            (adoption_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("durable adoption intent is missing")
        outcome_id = adoption_id + ":" + state
        existing = self._registry.execute(
            "SELECT base_revision, worker_ids, paths, artifact_path, artifact_digest, result_manifest, state FROM graph_workspace_adoptions WHERE adoption_id=?",
            (outcome_id,),
        ).fetchone()
        expected = (*row, state)
        if existing is not None:
            if tuple(existing) != expected:
                raise RuntimeError("durable adoption outcome contradicts its prior receipt")
            return
        with self._registry:
            self._registry.execute(
                """INSERT INTO graph_workspace_adoptions
                (adoption_id, base_revision, worker_ids, paths, artifact_path, artifact_digest, state, result_manifest)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (outcome_id, *row[:5], state, row[5]),
            )

    def _commit_tree_fingerprint(self, commit_sha: str, relative_path: str) -> str:
        entries = _git(self.project_root, "ls-tree", "-z", commit_sha, "--", relative_path).split(b"\\x00")
        for entry in entries:
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\\t", 1)
            if raw_path.decode("utf-8", errors="strict") != relative_path:
                continue
            mode, kind, object_sha = metadata.split()
            if kind != b"blob":
                raise RuntimeError("committed adoption path is not a regular Git blob")
            payload = _git(self.project_root, "cat-file", "blob", object_sha.decode("ascii"))
            if mode == b"120000":
                prefix = b"symlink" + bytes([0])
            elif mode.startswith(b"100"):
                prefix = b"file" + bytes([0])
            else:
                raise RuntimeError("committed adoption path has an unsupported Git mode")
            return hashlib.sha256(prefix + payload).hexdigest()
        return "missing"

    def _validate_commit_payload(self, payload: dict, receipt_digest: str) -> None:
        if payload.get("schema") != "auren-graph-adoption-commit-v1":
            raise RuntimeError("graph adoption commit receipt schema is unsupported")
        if self._canonical_digest(payload) != receipt_digest:
            raise RuntimeError("graph adoption commit receipt digest mismatch")
        adoption_id = payload.get("adoption_id")
        intent = self._registry.execute(
            """SELECT base_revision, worker_ids, paths, result_manifest
            FROM graph_workspace_adoptions WHERE adoption_id=? AND state='applying'""",
            (adoption_id,),
        ).fetchone()
        completed = self._registry.execute(
            "SELECT 1 FROM graph_workspace_adoptions WHERE adoption_id=? AND state='completed'",
            (str(adoption_id) + ":completed",),
        ).fetchone()
        if intent is None or completed is None:
            raise RuntimeError("commit receipt has no completed durable adoption intent")
        base, worker_ids_json, paths_json, manifest_json = intent
        paths = json.loads(paths_json)
        manifest = json.loads(manifest_json or "null")
        if (
            payload.get("base_revision") != base
            or payload.get("paths") != paths
            or payload.get("result_manifest") != manifest
            or not isinstance(manifest, dict)
            or set(manifest) != set(paths)
        ):
            raise RuntimeError("commit receipt does not match the completed adoption intent")
        commit_sha = payload.get("commit_sha")
        if not isinstance(commit_sha, str) or len(commit_sha) != 40:
            raise RuntimeError("commit receipt has an invalid commit SHA")
        parent = _git(self.project_root, "rev-parse", "--verify", commit_sha + "^{commit}^").decode().strip()
        if parent != base:
            raise RuntimeError("adoption commit parent does not match its pinned base")
        current = _git(self.project_root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
        _git(self.project_root, "merge-base", "--is-ancestor", commit_sha, current)
        changed = {
            item.decode("utf-8", errors="strict")
            for item in _git(self.project_root, "diff-tree", "--no-commit-id", "--name-only", "-z", "-r", "--no-renames", commit_sha, "--").split(b"\\x00")
            if item
        }
        if changed != set(paths):
            raise RuntimeError("adoption commit changed paths differ from the durable intent")
        for relative_path, expected_fingerprint in manifest.items():
            if self._commit_tree_fingerprint(commit_sha, relative_path) != expected_fingerprint:
                raise RuntimeError("adoption commit content differs from the verified result manifest")

    def _load_commit_receipts(self) -> None:
        self._committed_adoptions.clear()
        self._committed_workers.clear()
        rows = self._registry.execute(
            "SELECT adoption_id, base_revision, commit_sha, paths, result_manifest, receipt_digest FROM graph_workspace_adoption_commits ORDER BY adoption_id"
        ).fetchall()
        for adoption_id, base, commit_sha, paths_json, manifest_json, receipt_digest in rows:
            payload = {
                "schema": "auren-graph-adoption-commit-v1",
                "adoption_id": adoption_id,
                "base_revision": base,
                "commit_sha": commit_sha,
                "paths": json.loads(paths_json),
                "result_manifest": json.loads(manifest_json),
            }
            self._validate_commit_payload(payload, receipt_digest)
            self._committed_adoptions[adoption_id] = payload
            intent = self._registry.execute(
                "SELECT worker_ids FROM graph_workspace_adoptions WHERE adoption_id=? AND state='applying'",
                (adoption_id,),
            ).fetchone()
            for worker_id in json.loads(intent[0]):
                if worker_id in self._committed_workers and self._committed_workers[worker_id] != adoption_id:
                    raise RuntimeError("worker is bound to conflicting committed graph adoptions")
                self._committed_workers[worker_id] = adoption_id

    def _find_completed_adoption(self, worker_ids):
        wanted = tuple(sorted(worker_ids))
        matches = []
        rows = self._registry.execute(
            """SELECT a.adoption_id, a.base_revision, a.worker_ids, a.paths, a.result_manifest
            FROM graph_workspace_adoptions a
            WHERE a.state='applying'
            AND EXISTS (
                SELECT 1 FROM graph_workspace_adoptions o
                WHERE o.adoption_id = a.adoption_id || ':completed' AND o.state='completed'
            ) ORDER BY a.adoption_id"""
        ).fetchall()
        for adoption_id, base, workers_json, paths_json, manifest_json in rows:
            if tuple(sorted(json.loads(workers_json))) == wanted:
                matches.append((adoption_id, base, json.loads(workers_json), json.loads(paths_json), json.loads(manifest_json or "null")))
        if len(matches) > 1:
            raise RuntimeError("multiple completed adoptions match the same worker set")
        return matches[0] if matches else None

    def proposal_for(self, worker_id: str) -> WorkspaceProposal | None:
        """Return immutable proposal evidence restored from the durable registry."""
        with self._lock:
            return self._proposals.get(worker_id)

    def completed_adoption_for_workers(self, worker_ids):
        """Return an already completed, manifest-verified adoption without replaying its patch."""
        with self._lock:
            found = self._find_completed_adoption(worker_ids)
            if found is None:
                return None
            adoption_id, base, stored_workers, paths, manifest = found
            if not isinstance(manifest, dict) or set(manifest) != set(paths):
                raise RuntimeError("completed adoption has no valid immutable result manifest")
            receipt = self._committed_adoptions.get(adoption_id)
            if receipt is not None:
                if receipt.get("base_revision") != base or receipt.get("paths") != paths or receipt.get("result_manifest") != manifest:
                    raise RuntimeError("committed adoption receipt conflicts with its intent")
            else:
                current = _git(self.project_root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
                if current != base:
                    raise RuntimeError("completed adoption has a stale target HEAD and no commit receipt")
                tracked = {
                    item.decode("utf-8", errors="strict")
                    for item in _git(self.project_root, "diff", "--name-only", "-z", base, "--").split(b"\\x00")
                    if item
                }
                untracked = {
                    item.decode("utf-8", errors="strict")
                    for item in _git(self.project_root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\\x00")
                    if item
                }
                actual_paths = tracked | untracked
                if actual_paths != set(paths):
                    raise RuntimeError(
                        "completed adoption target differs from its recorded path set; "
                        + f"actual={sorted(actual_paths)} expected={sorted(set(paths))}"
                    )
                for relative_path, expected_fingerprint in manifest.items():
                    if _path_fingerprint(self.project_root, relative_path) != expected_fingerprint:
                        raise RuntimeError("completed adoption target differs from its immutable result manifest")
            workers = {}
            for worker_id in stored_workers:
                proposal = self._proposals.get(worker_id)
                if proposal is None:
                    raise RuntimeError("completed adoption proposal artifact is missing")
                workers[worker_id] = tuple(sorted(proposal.paths))
            return {
                "adoption_id": adoption_id, "base_revision": base,
                "paths": tuple(sorted(paths)), "workers": workers,
                "committed": receipt is not None,
            }

    def confirm_adoption_committed(self, worker_ids, *, commit_sha: str | None = None) -> dict:
        """Release the post-adoption worker gate only after validating a real Git commit receipt."""
        with self._lock:
            found = self._find_completed_adoption(worker_ids)
            if found is None:
                raise PermissionError("no completed adoption exists for the supplied worker set")
            adoption_id, base, stored_workers, paths, manifest = found
            existing = self._committed_adoptions.get(adoption_id)
            if existing is not None:
                self.base_revision = _git(self.project_root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
                return dict(existing)
            if not isinstance(manifest, dict) or set(manifest) != set(paths):
                raise RuntimeError("completed adoption has no valid immutable result manifest")
            current = _git(self.project_root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
            commit_sha = commit_sha or current
            if commit_sha != current:
                _git(self.project_root, "merge-base", "--is-ancestor", commit_sha, current)
            if _git(self.project_root, "status", "--porcelain", "--untracked-files=all").strip():
                raise PermissionError("target checkout must be clean before adoption commit receipt")
            payload = {
                "schema": "auren-graph-adoption-commit-v1",
                "adoption_id": adoption_id,
                "base_revision": base,
                "commit_sha": commit_sha,
                "paths": paths,
                "result_manifest": manifest,
            }
            receipt_digest = self._canonical_digest(payload)
            self._validate_commit_payload(payload, receipt_digest)
            with self._registry:
                self._registry.execute(
                    """INSERT INTO graph_workspace_adoption_commits
                    (adoption_id, base_revision, commit_sha, paths, result_manifest, receipt_digest)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (adoption_id, base, commit_sha,
                     json.dumps(paths, separators=(",", ":")),
                     json.dumps(manifest, sort_keys=True, separators=(",", ":")),
                     receipt_digest),
                )
            self._load_commit_receipts()
            self.base_revision = current
            self._reconcile_adoption_journal()
            return dict(self._committed_adoptions[adoption_id])

    def _reconcile_adoption_journal(self) -> None:
        self._recovered_adoption_pending_commit = False
        rows = self._registry.execute(
            "SELECT adoption_id, base_revision, worker_ids, paths, result_manifest FROM graph_workspace_adoptions WHERE state='applying'"
        ).fetchall()
        for adoption_id, base, worker_ids_json, paths_json, manifest_json in rows:
            outcomes = self._registry.execute(
                "SELECT state FROM graph_workspace_adoptions WHERE adoption_id IN (?, ?, ?)",
                (adoption_id + ":not_applied", adoption_id + ":blocked", adoption_id + ":completed"),
            ).fetchall()
            recorded = {row[0] for row in outcomes}
            if "completed" in recorded:
                expected_paths = set(json.loads(paths_json))
                manifest = json.loads(manifest_json or "null")
                if not isinstance(manifest, dict) or set(manifest) != expected_paths:
                    raise RuntimeError("completed adoption result manifest is missing or contradictory")
                receipt = self._committed_adoptions.get(adoption_id)
                if receipt is not None:
                    if receipt.get("base_revision") != base or set(receipt.get("paths", ())) != expected_paths or receipt.get("result_manifest") != manifest:
                        raise RuntimeError("committed adoption receipt contradicts its durable intent")
                    continue
                if base != self.base_revision:
                    raise RuntimeError("completed adoption has a stale target HEAD without a verified commit receipt")
                tracked = {
                    item.decode("utf-8", errors="strict")
                    for item in _git(self.project_root, "diff", "--name-only", "-z", base, "--").split(b"\\x00")
                    if item
                }
                untracked = {
                    item.decode("utf-8", errors="strict")
                    for item in _git(self.project_root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\\x00")
                    if item
                }
                actual_paths = tracked | untracked
                if actual_paths != expected_paths:
                    raise RuntimeError(
                        "completed adoption target differs from its recorded path set; "
                        + f"actual={sorted(actual_paths)} expected={sorted(expected_paths)}"
                    )
                for relative_path, expected_fingerprint in manifest.items():
                    if _path_fingerprint(self.project_root, relative_path) != expected_fingerprint:
                        raise RuntimeError("completed adoption target has drifted from its immutable result manifest")
                self._recovered_adoption_pending_commit = True
                continue
            if "blocked" in recorded:
                raise RuntimeError("interrupted adoption is blocked; manual reconciliation required")
            if "not_applied" in recorded:
                continue
            if base != self.base_revision:
                raise RuntimeError("interrupted adoption has a stale base revision")
            if _git(self.project_root, "status", "--porcelain", "--untracked-files=all").strip():
                self._record_adoption_outcome(adoption_id, "blocked")
                raise RuntimeError("interrupted adoption left a dirty target; manual reconciliation required")
            self._record_adoption_outcome(adoption_id, "not_applied")

    def _restore_registry(self) -> None:
        """Restore exact-base workers, plus only workers covered by a verified adoption commit receipt."""
        rows = self._registry.execute(
            "SELECT worker_id, base_revision, owned_paths, workspace_path, state, artifact_path, artifact_digest, proposal_paths, cancellation_reason, termination_evidence FROM graph_workspace_registry ORDER BY worker_id"
        ).fetchall()
        for worker_id, base, owned, workspace, state, artifact, digest, proposal_paths, reason, termination_evidence in rows:
            committed_id = self._committed_workers.get(worker_id)
            if base != self.base_revision:
                receipt = self._committed_adoptions.get(committed_id) if committed_id else None
                if (
                    receipt is None or receipt.get("base_revision") != base
                    or state not in {"adopted", "cleaned"}
                ):
                    raise RuntimeError("durable graph workspace registry is bound to a stale base revision; operator reconciliation required")
            path = Path(workspace)
            self._owners[worker_id] = tuple(json.loads(owned))
            self._leases[worker_id] = _RecoveredLease(path, base)
            if artifact:
                self._captured_artifacts[worker_id] = artifact
            if artifact and digest and proposal_paths:
                artifact_file = Path(artifact)
                if artifact_file.is_symlink() or not artifact_file.is_file():
                    raise RuntimeError("durable proposal artifact is missing or unsafe")
                patch = artifact_file.read_bytes()
                if hashlib.sha256(patch).hexdigest() != digest:
                    raise RuntimeError("durable proposal artifact digest mismatch")
                self._proposals[worker_id] = WorkspaceProposal(
                    worker_id, base, tuple(json.loads(proposal_paths)),
                    patch, digest, str(artifact_file),
                )
            if state == "cleaned":
                # Keep a tombstone and immutable proposal artifact after worktree removal.
                self._states[worker_id] = "cleaned"
                continue
            if not path.is_dir() or path.is_symlink():
                raise RuntimeError("durable graph workspace is missing or unsafe; refusing to treat it as empty")
            if _git(path, "rev-parse", "--verify", "HEAD^{commit}").decode().strip() != base:
                raise RuntimeError("durable graph worker worktree has drifted from its pinned base")
            restored_state = "unknown" if state == "running" else state
            self._states[worker_id] = restored_state
            if reason:
                self._cancel_reason = reason
            if termination_evidence:
                self._termination_evidence[worker_id] = termination_evidence
        if self._cancel_reason:
            self._cancel_event.set()

    def close(self) -> None:
        """Release the registry connection so callers can safely retire a coordinator."""
        with self._lock:
            registry = getattr(self, "_registry", None)
            if registry is not None:
                registry.close()
                self._registry = None

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
            if self._recovered_adoption_pending_commit:
                raise RuntimeError("commit recovered graph adoption before creating more workers")
            if worker_id in self._states:
                raise ValueError("worker_id already exists")
            for other_id, other_paths in self._owners.items():
                if self._states.get(other_id) == "cleaned":
                    continue
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
            self._persist_registry()
            return path

    def prepare_proposal(self, worker_id: str) -> WorkspaceProposal:
        try:
            return self._prepare_proposal(worker_id)
        except BaseException as exc:
            with self._lock:
                if worker_id in self._states and self._states[worker_id] not in {"adopted", "cleaned", "stopped"}:
                    self._states[worker_id] = "failed"
                self.cancel_siblings(f"proposal failed for {worker_id}: {type(exc).__name__}", except_worker_id=worker_id)
            raise

    def _prepare_proposal(self, worker_id: str) -> WorkspaceProposal:
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
                "scope_violation": bool(outside),
            }
            meta_path = artifact.with_suffix(".json")
            meta_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            os.chmod(meta_path, 0o600)
            self._captured_artifacts[worker_id] = str(artifact)
            if outside:
                raise PermissionError("worker changed paths outside declared ownership: " + ", ".join(outside))
            proposal = WorkspaceProposal(worker_id, self.base_revision, changed, patch, digest, str(artifact))
            self._proposals[worker_id] = proposal
            self._states[worker_id] = "proposal_ready"
            self._persist_registry()
            return proposal

    def adopt(self, proposal: WorkspaceProposal) -> tuple[str, ...]:
        """Apply one proposal; a failed adoption cancels sibling workers."""
        try:
            return self._adopt_locked(proposal)
        except BaseException as exc:
            with self._lock:
                worker_id = getattr(proposal, "worker_id", None)
                if worker_id in self._states and self._states[worker_id] not in {"adopted", "cleaned", "stopped"}:
                    self._states[worker_id] = "failed"
                self.cancel_siblings(f"adoption failed: {type(exc).__name__}", except_worker_id=worker_id)
            raise

    def adopt(self, proposal: WorkspaceProposal) -> tuple[str, ...]:
        """Adopt one proposal through the same serialized multi-proposal boundary."""
        return self.adopt_many((proposal,))[proposal.worker_id]

    def adopt_many(self, proposals: Iterable[WorkspaceProposal]) -> dict[str, tuple[str, ...]]:
        """Apply a verified sibling set once, against one clean base revision.

        All patches must be from this coordinator, have disjoint changed paths,
        and still target the exact clean source revision. No partial application
        is allowed: the combined patch is checked before a single apply operation.
        """
        rows = tuple(proposals)
        if any(not isinstance(proposal, WorkspaceProposal) for proposal in rows):
            raise TypeError("all adoption inputs must be coordinator-issued WorkspaceProposal values")
        applied = False
        try:
            with self._lock:
                if not rows:
                    raise ValueError("at least one proposal is required for adoption")
                if self._cancel_event.is_set():
                    raise RuntimeError(self._cancel_reason or "adoption blocked by cancellation")
                worker_ids = [proposal.worker_id for proposal in rows]
                if len(set(worker_ids)) != len(worker_ids):
                    raise ValueError("proposal worker ids must be unique")
                union_paths: set[str] = set()
                for proposal in rows:
                    known = self._proposals.get(proposal.worker_id)
                    if known != proposal or self._states.get(proposal.worker_id) != "proposal_ready":
                        raise PermissionError("proposal is not a registered ready artifact")
                    if hashlib.sha256(proposal.patch).hexdigest() != proposal.artifact_digest:
                        raise ValueError("proposal artifact digest mismatch")
                    artifact = Path(proposal.artifact_path)
                    if artifact.is_symlink() or not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != proposal.artifact_digest:
                        raise ValueError("persisted proposal artifact digest mismatch")
                    if proposal.base_revision != self.base_revision:
                        raise RuntimeError("stale proposal base revision")
                    owned = self._owners[proposal.worker_id]
                    if any(not any(path == root or path.startswith(root.rstrip("/") + "/") for root in owned) for path in proposal.paths):
                        raise PermissionError("proposal exceeds declared file ownership")
                    for path in proposal.paths:
                        if path in union_paths:
                            raise PermissionError(f"proposals overlap changed path: {path}")
                        union_paths.add(path)
                current = _git(self.project_root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
                if current != self.base_revision:
                    raise RuntimeError("stale target HEAD; refusing adoption")
                if _git(self.project_root, "status", "--porcelain", "--untracked-files=all").strip():
                    raise PermissionError("target checkout is not clean; refusing overlapping or uncommitted adoption")
                combined_patch = b"".join(
                    proposal.patch + (b"" if proposal.patch.endswith(b"\n") else b"\n")
                    for proposal in rows
                )
                _git(self.project_root, "apply", "--check", "--", input_bytes=combined_patch)
                adoption_id = self._persist_adoption_intent(tuple(worker_ids), tuple(sorted(union_paths)), combined_patch)
                _git(self.project_root, "apply", "--", input_bytes=combined_patch)
                applied = True
                adopted_paths = {
                    item.decode("utf-8", errors="strict") for item in _git(
                        self.project_root, "diff", "--name-only", "-z", self.base_revision, "--"
                    ).split(b"\x00") if item
                }
                adopted_paths.update(
                    item.decode("utf-8", errors="strict") for item in _git(
                        self.project_root, "ls-files", "--others", "--exclude-standard", "-z"
                    ).split(b"\x00") if item
                )
                if adopted_paths != union_paths:
                    raise RuntimeError("adopted path set differs from verified proposal set")
                result = {}
                for proposal in rows:
                    self._states[proposal.worker_id] = "adopted"
                    result[proposal.worker_id] = tuple(sorted(proposal.paths))
                self._persist_registry()
                self._record_adoption_outcome(adoption_id, "completed")
                self._recovered_adoption_pending_commit = True
                return result
        except BaseException as exc:
            rollback_error = None
            with self._lock:
                if applied:
                    try:
                        _git(self.project_root, "apply", "--reverse", "--", input_bytes=combined_patch)
                        if _git(self.project_root, "status", "--porcelain", "--untracked-files=all").strip():
                            raise RuntimeError("target checkout is still dirty after adoption rollback")
                    except BaseException as rollback_exc:
                        rollback_error = rollback_exc
                self.cancel_siblings(f"adoption failed: {type(exc).__name__}")
                for proposal in rows:
                    if proposal.worker_id in self._states and self._states[proposal.worker_id] not in {"cleaned", "stopped"}:
                        self._states[proposal.worker_id] = "failed"
                self._persist_registry()
            if rollback_error is not None:
                raise RuntimeError("adoption failed and rollback could not be verified; operator reconciliation required") from rollback_error
            raise

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
            self._persist_registry()
            return requested

    def mark_worker_stopped(self, worker_id: str) -> None:
        with self._lock:
            if worker_id not in self._states:
                raise KeyError(f"unknown worker: {worker_id}")
            if self._states[worker_id] != "cancel_requested":
                raise RuntimeError("worker stop may only be recorded after cancellation was requested")
            self._states[worker_id] = "stopped"
            self._persist_registry()

    def confirm_recovered_worker_stopped(self, worker_id: str, *, evidence: str) -> None:
        """Record explicit operator/runtime evidence before releasing an unknown lease."""
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("termination evidence is required")
        with self._lock:
            if self._states.get(worker_id) != "unknown":
                raise RuntimeError("only a recovered unknown worker requires termination confirmation")
            self._states[worker_id] = "stopped"
            self._termination_evidence[worker_id] = evidence.strip()[:2000]
            self._persist_registry()

    def cleanup_worker(self, worker_id: str) -> str:
        """Remove only a confirmed-stopped or completed isolated worktree."""
        with self._lock:
            if worker_id not in self._leases:
                raise KeyError(f"unknown worker: {worker_id}")
            state = self._states[worker_id]
            if state not in {"stopped", "proposal_ready", "adopted", "failed"}:
                raise RuntimeError("refusing to clean a worker that may still be running")
            lease = self._leases[worker_id]
            status = _git(lease.path, "status", "--porcelain", "--untracked-files=all").strip()
            if status and worker_id not in self._captured_artifacts:
                raise PermissionError("dirty worker has no persisted proposal artifact; refusing destructive cleanup")
            _git(self.project_root, "worktree", "remove", "--force", str(lease.path))
            self._states[worker_id] = "cleaned"
            self._persist_registry()
            return str(lease.path)

    def worker_states(self) -> dict[str, str]:
        with self._lock:
            return dict(sorted(self._states.items()))


__all__ = ["GraphWorkspaceCoordinator", "WorkspaceProposal"]
