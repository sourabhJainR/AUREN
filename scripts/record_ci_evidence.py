"""Capture CI completion as privacy-minimized engineering evidence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Mapping
import sys

from portable.engineering_evidence_capture import EvidenceCapture


_TRUTHY = {"1", "true", "yes", "on"}


def _external_sink_policy(environ: Mapping[str, str]) -> tuple[bool, bool]:
    url = environ.get("AUREN_EVIDENCE_SINK_URL", "").strip()
    token = environ.get("AUREN_EVIDENCE_SINK_TOKEN", "")
    if bool(url) != bool(token):
        raise ValueError("AUREN_EVIDENCE_SINK_URL and AUREN_EVIDENCE_SINK_TOKEN must be configured together")
    required = environ.get("AUREN_REQUIRE_EXTERNAL_EVIDENCE_SINK", "").strip().lower() in _TRUTHY
    return bool(url and token), required


def main() -> int:
    repository = os.environ.get("AUREN_CI_REPOSITORY", "")
    head_sha = os.environ.get("AUREN_CI_HEAD_SHA", "")
    run_id = os.environ.get("AUREN_CI_RUN_ID", "")
    attempt = os.environ.get("AUREN_CI_ATTEMPT", "1")
    job = os.environ.get("AUREN_CI_JOB", "")
    workflow = os.environ.get("AUREN_CI_WORKFLOW", "unknown")
    job_status = os.environ.get("AUREN_CI_STATUS", "unknown").lower()
    if not repository or len(head_sha) != 40 or not run_id or not job:
        raise ValueError("GitHub Actions repository, full head SHA, run ID, and job are required")
    if job_status not in {"success", "failure", "cancelled", "skipped"}:
        job_status = "unknown"

    sink_configured, sink_required = _external_sink_policy(os.environ)
    manifest = {
        "repository": repository,
        "head_sha": head_sha,
        "run_id": run_id,
        "attempt": attempt,
        "job": job,
        "workflow": workflow,
        "status": job_status,
    }
    artifact_digest = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    capture = EvidenceCapture.from_environment(
        Path(".auren") / "ci-engineering-evidence.sqlite3"
    )
    event_type = "regression_passed" if job_status == "success" else "regression_failed"
    event = capture.record(
        event_type,
        work_unit_id=f"ci:{run_id}:{attempt}",
        task_id=f"ci:{run_id}:{attempt}:{job}",
        iteration=int(attempt) if attempt.isdigit() else 0,
        status=job_status,
        repository=repository,
        head_sha=head_sha,
        artifact_digest=artifact_digest,
        source_type="ci",
        event_id=f"ci:{repository}:{run_id}:{attempt}:{job}",
        metadata={
            "phase": "workflow_completion",
            "test_suite": workflow,
            "run_id": f"{run_id}:{attempt}:{job}",
            "external_system": "github_actions",
            "result_category": job_status,
        },
    )
    output = Path(".auren") / "ci-engineering-evidence.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(event, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded {event_type} for {repository}@{head_sha}; event digest {event['event_digest']}")
    if not sink_configured:
        print(
            "WARNING: external evidence sink is not configured; this run is retained only in local/CI artifacts. "
            "Configure AUREN_EVIDENCE_SINK_URL and AUREN_EVIDENCE_SINK_TOKEN for durable external retention.",
            file=sys.stderr,
        )
        if sink_required:
            print("External evidence sink is required by policy; failing this evidence gate.", file=sys.stderr)
            return 2
    if capture.sink is not None and capture.pending_exports():
        print("Configured external evidence retention has pending exports", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
