"""Provision GitHub repositories into AUREN-owned local workspaces."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from .identity_compat import canonical_auren_home


_GITHUB_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def parse_github_repository_url(repository_url: str) -> tuple[str, str]:
    """Accept HTTPS GitHub repository URLs only; reject embedded credentials."""
    if not isinstance(repository_url, str) or not repository_url.strip():
        raise ValueError("repository URL is required")
    parsed = urlparse(repository_url.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("repository must be an HTTPS URL on github.com without embedded credentials")
    path = parsed.path.strip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    if not _GITHUB_REPOSITORY.fullmatch(path):
        raise ValueError("repository URL must have the form https://github.com/owner/repository")
    owner, name = path.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise ValueError("invalid GitHub repository path")
    return owner, name


def _git(args: list[str], *, cwd: Path | None = None, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # Do not echo command output: Git credential helpers may emit sensitive data.
        raise RuntimeError("Git operation failed; check Git installation and repository access") from exc
    return result.stdout.strip()


def provision_github_workspace(repository_url: str, branch: str | None = None) -> Path:
    """Clone a GitHub repository into AUREN-managed storage and initialize its profile.

    Existing managed clones are reused only when their origin matches and their
    worktree is clean. This function never resets, pulls over, or discards user work.
    """
    owner, name = parse_github_repository_url(repository_url)
    if branch is not None:
        if not isinstance(branch, str) or not branch.strip() or branch.startswith("-"):
            raise ValueError("branch must be a non-empty Git branch name")
        _git(["check-ref-format", "--branch", branch.strip()])
        branch = branch.strip()

    workspace_root = (canonical_auren_home() / "workspaces").resolve()
    target = (workspace_root / owner / name).resolve()
    if workspace_root not in target.parents:
        raise ValueError("managed workspace path escaped AUREN storage")
    target.parent.mkdir(parents=True, exist_ok=True)

    expected_origin = f"https://github.com/{owner}/{name}.git"
    if target.exists():
        if not (target / ".git").exists():
            raise RuntimeError(f"managed workspace exists but is not a Git checkout: {target}")
        origin = _git(["remote", "get-url", "origin"], cwd=target)
        normalized = origin.removesuffix(".git").rstrip("/").lower()
        expected = expected_origin.removesuffix(".git").lower()
        if normalized != expected:
            raise RuntimeError("existing managed workspace has a different Git origin; refusing to reuse it")
        if _git(["status", "--porcelain"], cwd=target):
            raise RuntimeError("managed workspace has local changes; commit or move them before reuse")
        if branch:
            current = _git(["branch", "--show-current"], cwd=target)
            if current != branch:
                raise RuntimeError(
                    f"managed workspace is on branch '{current}', not requested branch '{branch}'; "
                    "use a separate workspace for another branch"
                )
    else:
        command = ["clone", "--filter=blob:none", "--no-tags", "--single-branch"]
        if branch:
            command.extend(["--branch", branch])
        command.extend([expected_origin, str(target)])
        _git(command, timeout=600)

    # Verify the checkout after clone/reuse before writing AUREN metadata.
    _git(["rev-parse", "--verify", "HEAD"], cwd=target)
    from .onboarding import initialize_repository, load_project_profile

    profile_path = target / ".auren" / "project.json"
    if profile_path.exists() or profile_path.is_symlink():
        load_project_profile(target)
    else:
        initialize_repository(target, apply=True)
    return target
