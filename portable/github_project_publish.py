"""Publish a clean, initialized AUREN project to a new GitHub repository.

The GitHub CLI owns authentication. This command never stages or commits user
changes and defaults new repositories to private.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


def _normalize_github_remote(remote: str) -> str:
    """Return an exact owner/name pair for a GitHub HTTPS or SSH remote."""
    value = remote.strip()
    match = re.fullmatch(r"(?:[^@/:]+@)?github\.com:([^/]+)/([^/]+?)(?:\.git)?", value, re.IGNORECASE)
    if match:
        owner, name = match.groups()
    else:
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "ssh", "git"} or (parsed.hostname or "").lower() != "github.com":
            raise RuntimeError("origin must point to the requested repository on github.com")
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 2:
            raise RuntimeError("origin must contain exactly one GitHub owner/repository path")
        owner, name = parts
        if name.lower().endswith(".git"):
            name = name[:-4]
    if not owner or not name:
        raise RuntimeError("origin does not identify a GitHub owner/repository")
    return f"{owner}/{name}".lower()


def _run(args: Sequence[str], cwd: Path, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            list(args), cwd=cwd, check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "GitHub project publishing failed; check Git and GitHub CLI authentication"
        ) from exc
    return result.stdout.strip()


def publish_project(
    project_path: str | Path, repository: str, *, visibility: str = "private",
    description: str = "",
) -> dict:
    """Create a remote repository and push an existing clean local project."""
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise ValueError("repository must use owner/name format")
    if visibility not in {"private", "public", "internal"}:
        raise ValueError("visibility must be private, public or internal")
    if not isinstance(description, str) or len(description) > 350:
        raise ValueError("description must be text of at most 350 characters")
    raw = Path(project_path).expanduser()
    if raw.is_symlink():
        raise PermissionError("project path must not be a symlink")
    root = raw.resolve(strict=True)
    if not root.is_dir() or not (root / ".git").exists():
        raise ValueError("project path must be an initialized Git repository")
    if not shutil.which("gh"):
        raise FileNotFoundError("GitHub CLI (gh) is required; install it and run gh auth login")
    if _run(["git", "rev-parse", "--is-inside-work-tree"], root) != "true":
        raise ValueError("project path is not inside a Git worktree")
    branch = _run(["git", "branch", "--show-current"], root)
    if not branch:
        raise ValueError("project must have a named branch before publishing")
    status = _run(["git", "status", "--porcelain=v1", "--untracked-files=all"], root)
    if status:
        raise PermissionError(
            "project has uncommitted or untracked files; review and commit them explicitly before publishing"
        )
    remotes = _run(["git", "remote"], root).splitlines()
    if "origin" in remotes:
        raise FileExistsError("origin already exists; refusing to replace an existing remote")
    _run(["gh", "auth", "status"], root)
    args = ["gh", "repo", "create", repository, f"--{visibility}", "--source", str(root),
            "--remote", "origin", "--push"]
    if description:
        args.extend(["--description", description])
    _run(args, root, timeout=180)
    remote = _run(["git", "remote", "get-url", "origin"], root)
    try:
        view = json.loads(_run([
            "gh", "repo", "view", repository, "--json", "nameWithOwner,url,visibility,defaultBranchRef"
        ], root))
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("GitHub CLI returned invalid repository metadata after creation") from exc
    if not isinstance(view, dict) or view.get("nameWithOwner", "").lower() != repository.lower():
        raise RuntimeError("GitHub returned a repository identity that does not match the requested owner/name")
    if _normalize_github_remote(remote) != repository.lower():
        raise RuntimeError("GitHub repository was created but origin owner/name verification failed")
    pushed = _run(["git", "rev-parse", "HEAD"], root)
    remote_head = _run(["git", "ls-remote", "origin", f"refs/heads/{branch}"], root).split()
    if not remote_head or remote_head[0] != pushed:
        raise RuntimeError("remote branch does not match the local commit after push")
    return {
        "status": "published",
        "repository": view["nameWithOwner"],
        "url": view["url"],
        "visibility": view.get("visibility", visibility).lower(),
        "branch": branch,
        "head_commit": pushed,
        "remote": remote,
        "next_step": "Use this checkout with auren up, then submit work through the guarded AUREN task lifecycle.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auren handoff publish-project")
    parser.add_argument("--path", required=True)
    parser.add_argument("--repository", required=True, help="GitHub owner/name")
    parser.add_argument("--visibility", choices=("private", "public", "internal"), default="private")
    parser.add_argument("--description", default="")
    options = parser.parse_args(argv)
    try:
        result = publish_project(
            options.path, options.repository, visibility=options.visibility,
            description=options.description,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, PermissionError, FileExistsError) as exc:
        print(f"auren handoff publish-project blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
