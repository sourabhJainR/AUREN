"""Branch-aware multi-work-item engineering helpers for AUREN.

This module composes ordinary Git with AUREN's canonical context and memory owners.
It never performs blanket staging, silently resolves conflicts, or creates a second
durable knowledge store.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any, Iterable, Sequence


class GitWorkflowError(RuntimeError):
    """A Git workflow step failed without safe automatic recovery."""


class MergeConflictError(GitWorkflowError):
    """A merge needs a human or an explicit conflict-resolution workflow."""


@dataclass(frozen=True, slots=True)
class GitSnapshot:
    root: Path
    branch: str
    head_sha: str
    upstream: str | None
    changed_paths: tuple[str, ...]
    staged_paths: tuple[str, ...]
    dirty: bool


@dataclass(frozen=True, slots=True)
class SyncResult:
    remote_ref: str
    remote_sha: str
    fetched: bool
    merged: bool
    conflicts: tuple[str, ...] = ()


def _run_git(
    root: Path,
    *args: str,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise GitWorkflowError(f"Could not run Git: {exc}") from exc
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise GitWorkflowError(f"git {' '.join(args)} failed ({result.returncode}): {detail}")
    return result


def _paths_from_nul(output: str) -> tuple[str, ...]:
    return tuple(item for item in output.split("\0") if item)


def is_local_developer_setup(path: str | os.PathLike[str]) -> bool:
    """Identify machine-local setup/secrets, not shared project configuration."""
    value = PurePosixPath(str(path).replace("\\", "/"))
    parts = tuple(part.casefold() for part in value.parts)
    name = value.name.casefold()
    if any(part in {".venv", "venv", "env", "node_modules", ".idea", ".direnv"} for part in parts):
        return True
    if name == ".envrc" or name == "local.settings.json":
        return True
    if name.startswith(".env") and name not in {
        ".env.example", ".env.sample", ".env.template", ".env.test.example",
    }:
        return True
    if name in {"settings.json", "launch.json", "tasks.json"} and ".vscode" in parts:
        return True
    if name in {"devcontainer.local.json", "docker-compose.override.yml",
                "docker-compose.override.yaml", "compose.override.yml", "compose.override.yaml"}:
        return True
    if name.endswith((".user", ".suo")) or ".local." in name or name.endswith(".local"):
        return True
    return False


class ReadOnceFileCache:
    """Cache file bytes per path and filesystem signature for one engineering session.

    Repeated callers reuse decoded text and content digest. Metadata is re-statted so
    edits invalidate only the changed path; a caller can invalidate after a checkout,
    merge, generated-file refresh, or other operation that may preserve timestamps.
    This cache is disposable and is not a competing durable memory/index.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).resolve()
        self._entries: dict[str, tuple[tuple[int, int, int], bytes, str]] = {}
        self.content_reads = 0
        self.cache_hits = 0

    def _path(self, path: str | os.PathLike[str]) -> tuple[str, Path]:
        candidate = Path(path)
        absolute = (candidate if candidate.is_absolute() else self.root / candidate).resolve()
        try:
            relative = absolute.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise ValueError("cached repository reads must stay inside the repository root") from exc
        return relative, absolute

    def read_bytes(self, path: str | os.PathLike[str]) -> bytes:
        relative, absolute = self._path(path)
        stat = absolute.stat()
        signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        cached = self._entries.get(relative)
        if cached is not None and cached[0] == signature:
            self.cache_hits += 1
            return cached[1]
        content = absolute.read_bytes()
        import hashlib
        digest = hashlib.sha256(content).hexdigest()
        self._entries[relative] = (signature, content, digest)
        self.content_reads += 1
        return content

    def read_text(self, path: str | os.PathLike[str], *, encoding: str = "utf-8") -> str:
        return self.read_bytes(path).decode(encoding)

    def digest(self, path: str | os.PathLike[str]) -> str:
        relative, _ = self._path(path)
        self.read_bytes(relative)
        return self._entries[relative][2]

    def invalidate(self, paths: Iterable[str | os.PathLike[str]] | None = None) -> None:
        if paths is None:
            self._entries.clear()
            return
        for path in paths:
            relative, _ = self._path(path)
            self._entries.pop(relative, None)

    def stats(self) -> dict[str, int]:
        return {
            "cached_files": len(self._entries),
            "content_reads": self.content_reads,
            "cache_hits": self.cache_hits,
        }


class BranchAwareWorkspace:
    """Safe branch-aware Git operations for one local checkout."""

    def __init__(self, root: str | os.PathLike[str], *, remote: str = "origin") -> None:
        self.root = Path(root).resolve()
        self.remote = remote
        if not (self.root / ".git").exists() and _run_git(
            self.root, "rev-parse", "--git-dir", check=False
        ).returncode != 0:
            raise GitWorkflowError(f"Not a Git repository: {self.root}")
        self.read_cache = ReadOnceFileCache(self.root)
        self._cache_head = _run_git(self.root, "rev-parse", "HEAD").stdout.strip()

    def _refresh_cache_epoch(self) -> str:
        head = _run_git(self.root, "rev-parse", "HEAD").stdout.strip()
        if head != self._cache_head:
            self.read_cache.invalidate()
            self._cache_head = head
        return head

    def read_text(self, path: str | os.PathLike[str], *, encoding: str = "utf-8") -> str:
        """Read a repository file once per unchanged worktree/revision epoch."""
        self._refresh_cache_epoch()
        return self.read_cache.read_text(path, encoding=encoding)

    def snapshot(self) -> GitSnapshot:
        branch = _run_git(self.root, "branch", "--show-current").stdout.strip()
        head = self._refresh_cache_epoch()
        upstream_result = _run_git(
            self.root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False
        )
        upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else None
        status = _run_git(self.root, "status", "--porcelain=v1", "-z").stdout
        changed: list[str] = []
        staged: list[str] = []
        records = [record for record in status.split("\0") if record]
        index = 0
        while index < len(records):
            record = records[index]
            code, _, path = record.partition(" ")
            if path:
                changed.append(path)
                if code and code[0] not in {" ", "?"}:
                    staged.append(path)
                if code and code[1] in {"R", "C"} and index + 1 < len(records):
                    index += 1
                    changed.append(records[index])
            index += 1
        return GitSnapshot(
            self.root, branch, head, upstream, tuple(changed), tuple(staged), bool(records)
        )

    def _remote_sha(self, branch: str) -> str:
        ref = f"refs/heads/{branch}"
        result = _run_git(self.root, "ls-remote", "--heads", self.remote, ref)
        rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
        if not rows or len(rows[0]) < 2:
            raise GitWorkflowError(f"Remote branch {self.remote}/{branch} does not exist")
        return rows[0][0]

    def fetch_if_needed(self, branch: str, *, force_check: bool = False) -> SyncResult:
        """Contact the server only when its advertised branch SHA differs from cache."""
        remote_ref = f"{self.remote}/{branch}"
        advertised = self._remote_sha(branch)
        local = _run_git(self.root, "rev-parse", "--verify", remote_ref, check=False)
        local_sha = local.stdout.strip() if local.returncode == 0 else None
        fetched = force_check or local_sha != advertised
        if fetched:
            refspec = f"+refs/heads/{branch}:refs/remotes/{self.remote}/{branch}"
            _run_git(self.root, "fetch", "--no-tags", self.remote, refspec)
            refreshed = _run_git(self.root, "rev-parse", "--verify", remote_ref).stdout.strip()
            if refreshed != advertised:
                raise GitWorkflowError(
                    f"Remote branch moved during fetch: advertised {advertised}, fetched {refreshed}"
                )
        return SyncResult(remote_ref, advertised, fetched, False)

    def sync_current_with_remote(self, branch: str | None = None) -> SyncResult:
        """Fetch only when changed, then merge; conflicts remain visible and unresolved."""
        snapshot = self.snapshot()
        if snapshot.dirty:
            raise GitWorkflowError("Refusing remote sync with a dirty worktree or index")
        target = branch or snapshot.branch
        if not target:
            raise GitWorkflowError("Detached HEAD requires an explicit target branch")
        fetched = self.fetch_if_needed(target)
        merge_base = _run_git(
            self.root, "merge-base", "HEAD", fetched.remote_ref, check=False
        )
        if merge_base.returncode != 0:
            raise GitWorkflowError(f"No common history with {fetched.remote_ref}; refusing unrelated merge")
        ancestor = _run_git(
            self.root, "merge-base", "--is-ancestor", fetched.remote_ref, "HEAD", check=False
        )
        if ancestor.returncode == 0:
            return fetched
        outcome = _run_git(self.root, "merge", "--no-edit", fetched.remote_ref, check=False)
        if outcome.returncode:
            conflicts = _paths_from_nul(
                _run_git(self.root, "diff", "--name-only", "--diff-filter=U", "-z").stdout
            )
            self.read_cache.invalidate()
            raise MergeConflictError(
                f"Merge from {fetched.remote_ref} needs explicit conflict resolution: "
                + ", ".join(conflicts or ("unknown conflict",))
            )
        self._refresh_cache_epoch()
        return SyncResult(fetched.remote_ref, fetched.remote_sha, fetched.fetched, True)

    def start_task_branch(
        self,
        branch_name: str,
        *,
        feature_branch: str,
        target_branch: str = "main",
    ) -> SyncResult:
        """Create a task branch from a feature branch, then smart-merge latest target."""
        if not branch_name.strip() or branch_name.startswith("-"):
            raise ValueError("branch_name must be a non-empty branch name")
        snapshot = self.snapshot()
        if snapshot.dirty:
            raise GitWorkflowError("Commit or safely stash existing work before creating a task branch")
        if _run_git(self.root, "show-ref", "--verify", f"refs/heads/{branch_name}", check=False).returncode == 0:
            raise GitWorkflowError(f"Local branch already exists: {branch_name}")
        feature = self.fetch_if_needed(feature_branch)
        target = self.fetch_if_needed(target_branch)
        _run_git(self.root, "switch", "--create", branch_name, feature.remote_ref)
        self._refresh_cache_epoch()
        # The target may be ahead of the feature branch. Merge it instead of rebasing
        # shared work or overwriting the feature branch's history.
        base = _run_git(
            self.root, "merge-base", "HEAD", target.remote_ref, check=False
        )
        if base.returncode != 0:
            raise GitWorkflowError("Feature and target branches have no common history")
        target_is_ancestor = _run_git(
            self.root, "merge-base", "--is-ancestor", target.remote_ref, "HEAD", check=False
        )
        if target_is_ancestor.returncode == 0:
            return SyncResult(target.remote_ref, target.remote_sha, feature.fetched or target.fetched, False)
        merged = _run_git(self.root, "merge", "--no-edit", target.remote_ref, check=False)
        if merged.returncode:
            conflicts = _paths_from_nul(
                _run_git(self.root, "diff", "--name-only", "--diff-filter=U", "-z").stdout
            )
            self.read_cache.invalidate()
            raise MergeConflictError(
                f"Created {branch_name} from {feature.remote_ref}; latest {target.remote_ref} "
                f"needs explicit conflict resolution: " + ", ".join(conflicts or ("unknown conflict",))
            )
        self._refresh_cache_epoch()
        return SyncResult(target.remote_ref, target.remote_sha, feature.fetched or target.fetched, True)

    def stage_paths(self, paths: Sequence[str | os.PathLike[str]]) -> tuple[str, ...]:
        """Stage explicit paths only; local developer setup is never staged."""
        requested = tuple(str(path).replace("\\", "/") for path in paths)
        if not requested:
            return ()
        prohibited = tuple(path for path in requested if is_local_developer_setup(path))
        if prohibited:
            raise GitWorkflowError("Refusing to stage developer-local setup: " + ", ".join(prohibited))
        _run_git(self.root, "add", "--", *requested)
        staged = _paths_from_nul(_run_git(
            self.root, "diff", "--cached", "--name-only", "-z"
        ).stdout)
        prohibited_staged = tuple(path for path in staged if is_local_developer_setup(path))
        if prohibited_staged:
            head_exists = _run_git(self.root, "rev-parse", "--verify", "HEAD", check=False).returncode == 0
            if head_exists:
                _run_git(self.root, "reset", "-q", "HEAD", "--", *prohibited_staged, check=False)
            else:
                _run_git(self.root, "rm", "--cached", "-q", "--", *prohibited_staged, check=False)
            raise GitWorkflowError(
                "Removed prohibited developer-local files from the index: "
                + ", ".join(prohibited_staged)
            )
        return staged


def record_verified_jira_learning(
    memory: Any,
    *,
    jira_key: str,
    lesson: str,
    evidence_ids: Sequence[str],
    intent_digest: str,
    approved: bool = False,
    confidence: float = 0.8,
) -> Any:
    """Publish a verified reusable lesson through the canonical memory owner.

    No sidecar learning file is created. A lesson without evidence or approval is
    rejected by this boundary rather than promoted from an agent's private notes.
    """
    if not jira_key.strip():
        raise ValueError("jira_key is required")
    if not lesson.strip():
        raise ValueError("lesson is required")
    if not evidence_ids:
        raise ValueError("verified Jira learning requires evidence_ids")
    if not intent_digest.strip():
        raise ValueError("intent_digest is required")
    return memory.remember(
        "procedural",
        lesson.strip(),
        source=f"jira:{jira_key.strip().upper()}",
        task_id=jira_key.strip().upper(),
        evidence_ids=evidence_ids,
        intent_digest=intent_digest,
        confidence=confidence,
        verified=True,
        approved=approved,
    )


__all__ = [
    "BranchAwareWorkspace", "GitSnapshot", "GitWorkflowError", "MergeConflictError",
    "ReadOnceFileCache", "SyncResult", "is_local_developer_setup",
    "record_verified_jira_learning",
]
