"""Fail-closed pull-request freshness and shared-file integration guard.

This guard detects stale branches and overlapping open PRs before expensive CI.
It does not replace GitHub's merge queue, required checks, or review rules.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Any


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def commits_behind(base_ref: str, head_ref: str) -> int:
    """Count base commits not reachable from the PR head."""
    merge_base = git_output("merge-base", base_ref, head_ref)
    if not merge_base:
        raise ValueError("base and head have no merge base")
    return int(git_output("rev-list", "--count", f"{head_ref}..{base_ref}"))


def require_revision(ref: str, expected_sha: str, label: str) -> str:
    """Fail closed unless a ref still resolves to the event's exact revision."""
    actual = git_output("rev-parse", "--verify", f"{ref}^{{commit}}")
    if actual != expected_sha:
        raise ValueError(f"{label} ref changed: expected {expected_sha}, got {actual}")
    return actual


def overlapping_paths(current_paths: set[str], other_prs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return open PRs that edit any of the same paths, excluding this PR."""
    overlaps = []
    for pr in other_prs:
        number = pr.get("number")
        if not isinstance(number, int) or pr.get("state") != "open":
            continue
        shared = sorted(current_paths.intersection(set(pr.get("files", []))))
        if shared:
            overlaps.append({"number": number, "title": pr.get("title", ""), "paths": shared})
    return overlaps


def blocking_overlaps(current_pr: dict[str, Any], overlaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Serialize shared-file PRs in creation order without deadlocking both PRs.

    The oldest open PR owns the integration slot for overlapping paths. Later
    PRs fail closed until that PR is merged/closed, then must refresh their base
    and rerun checks. Missing ordering evidence blocks rather than guessing.
    """
    current_created = current_pr.get("created_at")
    current_number = current_pr.get("number")
    if not isinstance(current_created, str) or not isinstance(current_number, int):
        raise ValueError("current PR creation order is unavailable")
    current_order = (current_created, current_number)
    blocking = []
    for overlap in overlaps:
        created = overlap.get("created_at")
        number = overlap.get("number")
        if not isinstance(created, str) or not isinstance(number, int):
            raise ValueError("overlapping PR creation order is unavailable")
        if (created, number) < current_order:
            blocking.append(overlap)
    return blocking


class _SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep the CI bootstrap API client on api.github.com across redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.github.com"
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise urllib.error.HTTPError(
                req.full_url, code, "redirect outside trusted GitHub API host", headers, fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def api_json(url: str, token: str) -> Any:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("PR integration API URL is outside the trusted GitHub API host")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    opener = urllib.request.build_opener(_SameHostRedirectHandler())
    with opener.open(request, timeout=20) as response:
        return json.load(response)


def api_list(url: str, token: str) -> list[dict[str, Any]]:
    """Read every page of a GitHub list endpoint; truncation must not hide overlap."""
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key not in {"page", "per_page"}]
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        paged_query = urlencode(query + [("per_page", "100"), ("page", str(page))])
        page_url = urlunsplit((parts.scheme, parts.netloc, parts.path, paged_query, parts.fragment))
        result = api_json(page_url, token)
        if not isinstance(result, list):
            raise ValueError("GitHub list endpoint returned a non-list response")
        items.extend(result)
        if len(result) < 100:
            return items
        page += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args()

    try:
        require_revision(args.base_ref, args.base_sha, "base")
        require_revision(args.head_ref, args.head_sha, "head")
        behind = commits_behind(args.base_ref, args.head_ref)
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ValueError("GITHUB_TOKEN is required")
        root = f"https://api.github.com/repos/{args.repo}"
        current_files = api_list(f"{root}/pulls/{args.pr}/files", token)
        current_paths = {item["filename"] for item in current_files}
        open_prs = api_list(f"{root}/pulls?state=open", token)
        current_pr = next((pr for pr in open_prs if pr.get("number") == args.pr), None)
        if current_pr is None:
            raise ValueError("current PR is missing from the complete open-PR listing")
        overlaps = []
        base_branch_name = args.base_ref.rsplit("/", 1)[-1]
        for pr in open_prs:
            if pr.get("number") == args.pr or pr.get("base", {}).get("ref") != base_branch_name:
                continue
            changed = api_list(f"{root}/pulls/{pr['number']}/files", token)
            shared = sorted(current_paths.intersection(item["filename"] for item in changed))
            if shared:
                overlaps.append({
                    "number": pr["number"],
                    "title": pr["title"],
                    "created_at": pr.get("created_at"),
                    "paths": shared,
                })
        blockers = blocking_overlaps(current_pr, overlaps)
        print(json.dumps({
            "pull_request": args.pr,
            "head_sha": args.head_sha,
            "base_sha": args.base_sha,
            "commits_behind_base": behind,
            "overlapping_pull_requests": overlaps,
            "blocking_overlaps": blockers,
            "integration_order": "oldest open overlapping PR first",
        }, indent=2))
        failures = []
        if behind:
            failures.append(f"branch is {behind} commit(s) behind {args.base_ref}; rebase/update and rerun")
        if blockers:
            detail = "; ".join(
                f"#{item['number']} ({', '.join(item['paths'])})" for item in blockers
            )
            failures.append(f"an older open PR owns overlapping paths: {detail}; wait for it to merge/close, refresh base, and rerun")
        if failures:
            print("PR integration guard failed: " + " | ".join(failures), file=sys.stderr)
            return 1
        print("PR integration guard passed: fresh base and no shared-file overlap.")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError, urllib.error.URLError, KeyError, TypeError) as exc:
        print(f"PR integration guard could not prove safety; failing closed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
