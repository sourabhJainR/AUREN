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


def api_json(url: str, token: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
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
        actual_head = git_output("rev-parse", args.head_ref)
        if actual_head != args.head_sha:
            raise ValueError(f"checked-out head changed: expected {args.head_sha}, got {actual_head}")
        behind = commits_behind(args.base_ref, args.head_ref)
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ValueError("GITHUB_TOKEN is required")
        root = f"https://api.github.com/repos/{args.repo}"
        current_files = api_list(f"{root}/pulls/{args.pr}/files", token)
        current_paths = {item["filename"] for item in current_files}
        open_prs = api_list(f"{root}/pulls?state=open", token)
        overlaps = []
        base_branch_name = args.base_ref.rsplit("/", 1)[-1]
        for pr in open_prs:
            if pr.get("number") == args.pr or pr.get("base", {}).get("ref") != base_branch_name:
                continue
            changed = api_list(f"{root}/pulls/{pr['number']}/files", token)
            shared = sorted(current_paths.intersection(item["filename"] for item in changed))
            if shared:
                overlaps.append({"number": pr["number"], "title": pr["title"], "paths": shared})
        print(json.dumps({
            "pull_request": args.pr,
            "head_sha": args.head_sha,
            "base_sha": args.base_sha,
            "commits_behind_base": behind,
            "overlapping_pull_requests": overlaps,
        }, indent=2))
        failures = []
        if behind:
            failures.append(f"branch is {behind} commit(s) behind {args.base_ref}; rebase/update and rerun")
        if overlaps:
            detail = "; ".join(
                f"#{item['number']} ({', '.join(item['paths'])})" for item in overlaps
            )
            failures.append(f"shared-file work is active in another open PR: {detail}; serialize integration or split ownership")
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
