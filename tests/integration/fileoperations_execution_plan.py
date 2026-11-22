"""Exercise AUREN's durable plan boundary against a real FileOperations checkout."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

AUREN_ROOT = Path(os.environ["AUREN_ROOT"]).resolve()
FILEOPERATIONS_ROOT = Path(os.environ["FILEOPERATIONS_ROOT"]).resolve()
sys.path.insert(0, str(AUREN_ROOT))

from portable.execution_controller import ExecutionController
from portable.guarded_filesystem import write_workspace_text


def _write_file(payload):
    write_workspace_text(payload["root"], payload["path"], "written under canonical execution plan")
    criteria = payload["_auren_execution_plan"]["acceptance"]
    return {
        "success": True, "detail": "scoped file write completed", "evidence": ["guarded-filesystem"],
        "acceptance_evidence": {criterion: ["guarded-filesystem"] for criterion in criteria},
    }


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(FILEOPERATIONS_ROOT), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    repository = "sourabhJainR/FileOperations"
    branch = _git("branch", "--show-current")
    head = _git("rev-parse", "HEAD")
    if branch != "main":
        raise RuntimeError(f"expected main branch checkout, got {branch!r}")
    os.environ["AUREN_REPOSITORY_SLUG"] = repository

    plan = {
        "target": {
            "repository": repository,
            "branch": branch,
            "base_sha": head,
            "allowed_paths": ["apps/filekit/.auren-validation/**"],
            "governance_repository": "sourabhJainR/AUREN",
        },
        "requirements": ["FILEKIT-PLAN-001"],
        "acceptance": [
            "AUREN can write within the declared FileOperations workspace scope.",
            "AUREN blocks writes outside the declared scope before file mutation.",
        ],
        "required_capabilities": ["workspace_filesystem"],
        "capabilities": [{
            "name": "workspace_filesystem",
            "available": True,
            "evidence": "FileOperations cross-repository CI workspace",
        }],
        "stopping_condition": "Scoped write succeeds and out-of-scope write is durably rejected.",
    }

    controller = ExecutionController(FILEOPERATIONS_ROOT)
    controller.register_handler("fileoperations_plan_write", _write_file, execution_class="read_only")
    validation_dir = FILEOPERATIONS_ROOT / "apps/filekit/.auren-validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    marker = validation_dir / "allowed.txt"
    try:
        allowed = controller.submit(
            "fileoperations_plan_write",
            {"root": str(FILEOPERATIONS_ROOT), "path": "apps/filekit/.auren-validation/allowed.txt"},
            work_key=f"fileoperations-plan-allowed:{head}",
            execution_plan=plan,
        )
        controller.run_once(allowed.id)
        if controller.task(allowed.id).state != "completed" or not marker.is_file():
            raise AssertionError("in-scope FileOperations write did not complete")

        protected_file = FILEOPERATIONS_ROOT / "apps/filekit/index.html"
        before = _sha256(protected_file)
        denied = controller.submit(
            "fileoperations_plan_write",
            {"root": str(FILEOPERATIONS_ROOT), "path": "apps/filekit/index.html"},
            work_key=f"fileoperations-plan-denied:{head}",
            execution_plan=plan,
        )
        try:
            controller.run_once(denied.id)
        except PermissionError:
            pass
        else:
            raise AssertionError("out-of-scope FileOperations write was not rejected")
        if controller.task(denied.id).state != "failed":
            raise AssertionError("out-of-scope task did not persist a failed terminal state")
        if _sha256(protected_file) != before:
            raise AssertionError("out-of-scope write modified the protected FileOperations file")

        infeasible_plan = dict(plan)
        infeasible_plan["requirements"] = [
            "Build live market quotes.",
            "Use a free provider with 15-minute delayed quotes.",
            "Show delayed data as real-time.",
        ]
        infeasible_key = f"fileoperations-infeasible:{head}"
        try:
            controller.submit(
                "fileoperations_plan_write",
                {"root": str(FILEOPERATIONS_ROOT), "path": "apps/filekit/.auren-validation/infeasible.txt"},
                work_key=infeasible_key,
                execution_plan=infeasible_plan,
            )
        except PermissionError:
            pass
        else:
            raise AssertionError("contradictory FileOperations requirement was admitted")
        if controller.task_by_work_key(infeasible_key) is not None:
            raise AssertionError("infeasible FileOperations requirement created a durable task")

        missing_privacy_policy = dict(plan)
        missing_privacy_policy["requirements"] = [
            "Export personal data to an external analytics service.",
        ]
        privacy_key = f"fileoperations-privacy-policy:{head}"
        try:
            controller.submit(
                "fileoperations_plan_write",
                {"root": str(FILEOPERATIONS_ROOT), "path": "apps/filekit/.auren-validation/private.txt"},
                work_key=privacy_key,
                execution_plan=missing_privacy_policy,
            )
        except PermissionError:
            pass
        else:
            raise AssertionError("sensitive-data requirement without a privacy policy was admitted")
        if controller.task_by_work_key(privacy_key) is not None:
            raise AssertionError("privacy-sensitive requirement created a durable task")

        consent_required = dict(plan)
        consent_required["requirements"] = ["Transfer personal data to an external service."]
        consent_required["constraints"] = [
            "Encrypt transferred data and enforce role-based access control.",
        ]
        consent_required["acceptance"] = [
            "Do not transfer personal data until the user consents.",
        ]
        consent_key = f"fileoperations-consent-gate:{head}"
        try:
            controller.submit(
                "fileoperations_plan_write",
                {"root": str(FILEOPERATIONS_ROOT), "path": "apps/filekit/.auren-validation/consent.txt"},
                work_key=consent_key,
                execution_plan=consent_required,
            )
        except PermissionError as exc:
            if "approval_gated" not in str(exc):
                raise
        else:
            raise AssertionError("consent-bound transfer bypassed the trusted approval gate")
        if controller.task_by_work_key(consent_key) is not None:
            raise AssertionError("consent-bound transfer created a durable task without approval")

        print(json.dumps({
            "repository": repository,
            "head_sha": head,
            "plan_digest": controller.task(allowed.id).payload["_auren_execution_plan_digest"],
            "in_scope_task": controller.task(allowed.id).state,
            "out_of_scope_task": controller.task(denied.id).state,
            "protected_file_unchanged": True,
            "infeasible_requirement_rejected": True,
            "privacy_policy_required": True,
            "consent_gate_required": True,
            "result": "passed",
        }, sort_keys=True))
    finally:
        shutil.rmtree(validation_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
