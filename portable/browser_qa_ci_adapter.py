"""Bounded GitHub Actions browser-QA polling and evidence receipt adapter."""
from __future__ import annotations
import json, re, time, urllib.request
from pathlib import Path
from typing import Callable

SHA = re.compile(r"^[0-9a-f]{40}$")
TERMINAL = {"success": "passed", "failure": "failed", "cancelled": "cancelled",
            "timed_out": "failed", "action_required": "blocked", "neutral": "blocked",
            "skipped": "blocked"}

class BrowserQAPollError(RuntimeError): pass

def poll_browser_qa(repo: str, run_id: int, expected_sha: str, *, token: str,
                    timeout_seconds: int = 900, interval_seconds: int = 15,
                    request: Callable[[str, str], dict] | None = None,
                    clock: Callable[[], float] = time.monotonic,
                    sleep: Callable[[float], None] = time.sleep) -> dict:
    parts = repo.split("/") if isinstance(repo, str) else []
    if len(parts) != 2 or any(not part or any(ch.isspace() for ch in part) for part in parts): raise ValueError("invalid repository slug")
    if type(run_id) is not int or run_id <= 0: raise ValueError("run_id must be positive")
    if not SHA.fullmatch(expected_sha): raise ValueError("expected_sha must be a full commit SHA")
    if not token: raise ValueError("token is required")
    if timeout_seconds < 1 or interval_seconds < 1: raise ValueError("poll bounds must be positive")
    def default_request(url, token):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        if not url.startswith("https://api.github.com/repos/"):
            raise ValueError("CI polling is restricted to GitHub's HTTPS API host")
        req=urllib.request.Request(url, headers={"Accept":"application/vnd.github+json",
          "Authorization":f"Bearer {token}", "X-GitHub-Api-Version":"2022-11-28"})
        opener=urllib.request.build_opener(NoRedirect())
        with opener.open(req, timeout=20) as response: return json.loads(response.read())
    fetch=request or default_request
    url=f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"
    deadline=clock()+timeout_seconds
    while True:
        run=fetch(url, token)
        observed=run.get("head_sha","")
        receipt={"provider":"github-actions","repository":repo,"run_id":run_id,
          "run_url":run.get("html_url"),"expected_sha":expected_sha,"observed_sha":observed,
          "status":run.get("status"),"conclusion":run.get("conclusion")}
        if observed != expected_sha: receipt["state"]="sha_mismatch"; return receipt
        if run.get("status")=="completed":
            receipt["state"]=TERMINAL.get(run.get("conclusion"),"blocked"); return receipt
        if clock()>=deadline: receipt["state"]="timeout"; return receipt
        sleep(min(interval_seconds,max(0,deadline-clock())))

def persist_receipt(receipt: dict, path: str | Path) -> Path:
    destination=Path(path); destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_suffix(destination.suffix+".tmp")
    temporary.write_text(json.dumps(receipt,sort_keys=True,indent=2),encoding="utf-8")
    temporary.replace(destination)
    return destination
