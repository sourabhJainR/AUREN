"""Trusted live repository context provider for consequential execution gates.

Repository identity and candidate SHA are read from Git, never task payloads or
self-reported decision records. Decision records and approver policy are loaded
from operator-controlled paths; evidence IDs are resolved against the canonical
ledger for that exact repository/head.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from .canonical_evidence_ledger import CanonicalEvidenceLedger


_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class TrustedRepositoryContextProvider:
    """Callable provider registered as the execution controller's context handler.

    The configured repository slug, decision-record directory, approver-policy
    file, and evidence ledger are operator-owned. It refuses dirty worktrees,
    ambiguous remotes, malformed policies, and records for stale heads.
    """

    def __init__(
        self,
        repository_root: str | Path,
        *,
        expected_repository: str,
        decision_record_dir: str | Path,
        authorized_approvers_file: str | Path,
        evidence_ledger: CanonicalEvidenceLedger,
        require_clean_worktree: bool = True,
    ) -> None:
        self.root = Path(repository_root).expanduser().resolve()
        self.expected_repository = expected_repository.strip()
        if not _REPOSITORY_RE.fullmatch(self.expected_repository):
            raise ValueError("expected_repository must be an owner/repository slug")
        self.decision_record_dir = Path(decision_record_dir).expanduser().resolve()
        self.authorized_approvers_file = Path(authorized_approvers_file).expanduser().resolve()
        self.evidence_ledger = evidence_ledger
        self.require_clean_worktree = bool(require_clean_worktree)

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return completed.stdout.strip()

    @staticmethod
    def _remote_slug(remote: str) -> str:
        remote = remote.strip()
        if remote.startswith("git@") and ":" in remote:
            slug = remote.split(":", 1)[1]
        elif remote.startswith("ssh://"):
            path = remote.split("/", 3)[-1]
            slug = path
        elif remote.startswith("https://") or remote.startswith("http://"):
            from urllib.parse import urlparse
            slug = urlparse(remote).path.lstrip("/")
        else:
            raise ValueError("origin remote uses an unsupported or ambiguous URL form")
        slug = slug.removesuffix(".git").strip("/")
        if not _REPOSITORY_RE.fullmatch(slug):
            raise ValueError("origin remote does not resolve to an owner/repository slug")
        return slug

    def __call__(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise ValueError("trusted context request must be a mapping")
        task_id = request.get("task_id")
        if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
            raise ValueError("trusted context requires a safe task identifier")
        # Caller-provided repository/head fields are intentionally ignored.
        remote = self._git("remote", "get-url", "origin")
        actual_repository = self._remote_slug(remote)
        if actual_repository.casefold() != self.expected_repository.casefold():
            raise ValueError("live origin repository does not match operator policy")
        head = self._git("rev-parse", "--verify", "HEAD^{commit}")
        if not _COMMIT_RE.fullmatch(head):
            raise ValueError("live HEAD is not a full commit SHA")
        if self.require_clean_worktree:
            status = self._git("status", "--porcelain", "--untracked-files=all")
            if status:
                raise ValueError("promotion blocked because the live worktree is dirty")

        record_path = (self.decision_record_dir / f"{task_id}.json").resolve()
        if record_path.parent != self.decision_record_dir:
            raise ValueError("decision record path escaped operator-controlled directory")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("task_id") != task_id:
            raise ValueError("operator decision record is missing or belongs to another task")

        approvers_data = json.loads(self.authorized_approvers_file.read_text(encoding="utf-8"))
        if not isinstance(approvers_data, list) or not approvers_data or any(
            not isinstance(item, str) or not item.strip() for item in approvers_data
        ):
            raise ValueError("operator approver policy must be a non-empty string list")
        known_evidence_ids = self.evidence_ledger.known_evidence_ids(
            repository=self.expected_repository, head_sha=head
        )
        return {
            "decision_record": record,
            "expected_repository": self.expected_repository,
            "expected_head": head,
            "authorized_approvers": tuple(approvers_data),
            "known_evidence_ids": known_evidence_ids,
        }


def trusted_repository_context(request: Mapping[str, Any]) -> dict[str, Any]:
    """Importable, environment-configured adapter for ExecutionController.

    Set deployment paths and repository identity in service configuration. This
    module-level function is durable across controller restarts; task payload
    values are never used to select the repository, head, decision file, or policy.
    """
    root = os.environ.get("AUREN_REPOSITORY_ROOT", "").strip()
    repository = os.environ.get("AUREN_REPOSITORY_SLUG", "").strip()
    record_dir = os.environ.get("AUREN_DECISION_RECORD_DIR", "").strip()
    approvers_file = os.environ.get("AUREN_AUTHORIZED_APPROVERS_FILE", "").strip()
    ledger_path = os.environ.get("AUREN_CANONICAL_EVIDENCE_DB", "").strip()
    if not all((root, repository, record_dir, approvers_file)):
        raise ValueError(
            "trusted context requires AUREN_REPOSITORY_ROOT, AUREN_REPOSITORY_SLUG, "
            "AUREN_DECISION_RECORD_DIR, and AUREN_AUTHORIZED_APPROVERS_FILE"
        )
    ledger = CanonicalEvidenceLedger(
        ledger_path or str(Path(root).expanduser().resolve() / ".auren" / "canonical-evidence.sqlite3")
    )
    provider = TrustedRepositoryContextProvider(
        root,
        expected_repository=repository,
        decision_record_dir=record_dir,
        authorized_approvers_file=approvers_file,
        evidence_ledger=ledger,
    )
    return provider(request)


__all__ = ["TrustedRepositoryContextProvider", "trusted_repository_context"]
