"""Bounded, read-only GitHub Actions browser-QA polling."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

SHA = re.compile(r"^[0-9a-f]{40}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKFLOW_PATH_PREFIX = ".github/workflows/"
TERMINAL = {
    "success": "passed",
    "failure": "failed",
    "cancelled": "cancelled",
    "timed_out": "failed",
    "action_required": "blocked",
    "neutral": "blocked",
    "skipped": "blocked",
}


class BrowserQAPollError(RuntimeError):
    pass


def poll_browser_qa(
    repo: str,
    run_id: int,
    expected_sha: str,
    *,
    token: str,
    expected_workflow_path: str,
    timeout_seconds: int = 900,
    interval_seconds: int = 15,
    request: Callable[[str, str], dict] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    if not isinstance(repo, str) or not REPO.fullmatch(repo) or any(part in {".", ".."} for part in repo.split("/")):
        raise ValueError("invalid repository slug")
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("run_id must be positive")
    if not SHA.fullmatch(expected_sha):
        raise ValueError("expected_sha must be a full commit SHA")
    if not isinstance(token, str) or not token:
        raise ValueError("token is required")
    if (
        not isinstance(expected_workflow_path, str)
        or not expected_workflow_path.startswith(WORKFLOW_PATH_PREFIX)
        or "\\" in expected_workflow_path
        or any(part in {"", ".", ".."} for part in expected_workflow_path.split("/"))
    ):
        raise ValueError("expected_workflow_path must be a safe .github/workflows path")
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise ValueError("timeout_seconds must be a positive integer")
    if type(interval_seconds) is not int or interval_seconds < 1:
        raise ValueError("interval_seconds must be a positive integer")

    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"

    def default_request(url: str, token: str, request_timeout: float) -> dict:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        if not url.startswith("https://api.github.com/repos/"):
            raise ValueError("CI polling is restricted to GitHub's HTTPS API host")
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(req, timeout=request_timeout) as response:
            return json.loads(response.read(65536))

    deadline = clock() + timeout_seconds
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            return {
                "provider": "github-actions",
                "repository": repo,
                "run_id": run_id,
                "run_url": None,
                "expected_sha": expected_sha,
                "observed_sha": None,
                "expected_workflow_path": expected_workflow_path,
                "observed_workflow_path": None,
                "status": None,
                "conclusion": None,
                "state": "timeout",
            }
        try:
            run = request(url, token) if request is not None else default_request(
                url, token, min(20.0, remaining)
            )
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            state = "timeout" if clock() >= deadline else "blocked"
            return {
                "provider": "github-actions",
                "repository": repo,
                "run_id": run_id,
                "run_url": None,
                "expected_sha": expected_sha,
                "observed_sha": None,
                "expected_workflow_path": expected_workflow_path,
                "observed_workflow_path": None,
                "status": None,
                "conclusion": None,
                "transport_error": type(exc).__name__,
                "state": state,
            }

        observed = run.get("head_sha", "")
        observed_path = run.get("path")
        receipt = {
            "provider": "github-actions",
            "repository": repo,
            "run_id": run_id,
            "run_url": run.get("html_url"),
            "expected_sha": expected_sha,
            "observed_sha": observed,
            "expected_workflow_path": expected_workflow_path,
            "observed_workflow_path": observed_path,
            "workflow_id": run.get("workflow_id"),
            "workflow_name": run.get("name"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
        }
        if observed_path != expected_workflow_path:
            receipt["state"] = "workflow_mismatch"
            return receipt
        if observed != expected_sha:
            receipt["state"] = "sha_mismatch"
            return receipt
        if run.get("status") == "completed":
            receipt["state"] = TERMINAL.get(run.get("conclusion"), "blocked")
            return receipt
        remaining = deadline - clock()
        if remaining <= 0:
            receipt["state"] = "timeout"
            return receipt
        sleep(min(interval_seconds, remaining))


def persist_receipt(receipt: dict, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return destination
