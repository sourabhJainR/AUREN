from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / ".ai-harness"
sys.path.insert(0, str(HARNESS))

import engine
from portable.engineering_design_guard import PRINCIPLE_FAMILY_IDS


def principle_receipt(intent_digest: str, revision: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "auren-engineering-principle-verification-v1",
        "intent_digest": intent_digest,
        "code_revision": revision,
        "reviewer_id": "external-reviewer",
        "reviewer_version": "v1",
        "families": {
            family: {
                "status": "verified",
                "reason": f"Verified {family} against the tested workspace snapshot.",
                "evidence_refs": [f"evidence:{family}:{revision}"],
            }
            for family in PRINCIPLE_FAMILY_IDS
        },
    }
    payload["digest"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def test_workspace_revision_digest_changes_when_source_bytes_change():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "sample.py"
        source.write_text("VALUE = 1\n", encoding="utf-8")
        with patch.object(engine, "ROOT", root), patch.object(engine, "changed_files", return_value=["sample.py"]), patch.object(
            engine, "run_capture", return_value=(0, "base-commit")
        ):
            first = engine.workspace_revision_digest()
            source.write_text("VALUE = 2\n", encoding="utf-8")
            second = engine.workspace_revision_digest()
        assert first.startswith("workspace-sha256:")
        assert second.startswith("workspace-sha256:")
        assert first != second


def test_final_revision_gate_requires_executable_validation_for_code_changes():
    manifest = {
        "validation": {"passed": True, "results": [{"command": ["pytest"], "status": "passed"}]},
        "verified_code_revision": "workspace-sha256:revision-a",
        "review_candidate_revision": "workspace-sha256:revision-a",
    }
    result = engine.validate_final_revision_evidence(
        manifest,
        risk="medium",
        intent_digest="intent-a",
        changed_paths=["portable/helper.py"],
        final_revision="workspace-sha256:revision-a",
    )
    assert result["passed"]
    assert result["exact_revision_passed"]

    no_results = dict(manifest)
    no_results["validation"] = {"passed": True, "results": []}
    result = engine.validate_final_revision_evidence(
        no_results,
        risk="medium",
        intent_digest="intent-a",
        changed_paths=["portable/helper.py"],
        final_revision="workspace-sha256:revision-a",
    )
    assert not result["passed"]
    assert any("executable results" in blocker for blocker in result["blockers"])


def test_high_risk_final_revision_gate_requires_complete_principle_receipt():
    revision = "workspace-sha256:revision-b"
    intent = "intent-b"
    manifest = {
        "validation": {"passed": True, "results": [{"command": ["pytest"], "status": "passed"}]},
        "verified_code_revision": revision,
        "review_candidate_revision": revision,
    }
    missing = engine.validate_final_revision_evidence(
        manifest, risk="high", intent_digest=intent,
        changed_paths=["portable/security.py"], final_revision=revision,
    )
    assert not missing["passed"]
    assert missing["principle_verification_passed"] is False

    manifest["principle_verification"] = principle_receipt(intent, revision)
    valid = engine.validate_final_revision_evidence(
        manifest, risk="high", intent_digest=intent,
        changed_paths=["portable/security.py"], final_revision=revision,
    )
    assert valid["passed"], valid["blockers"]
    assert valid["principle_verification_passed"] is True


def test_final_revision_gate_rejects_source_changed_after_review():
    revision = "workspace-sha256:revision-final"
    manifest = {
        "validation": {"passed": True, "results": [{"command": ["pytest"], "status": "passed"}]},
        "verified_code_revision": revision,
        "review_candidate_revision": "workspace-sha256:revision-reviewed",
    }
    result = engine.validate_final_revision_evidence(
        manifest, risk="medium", intent_digest="intent-c",
        changed_paths=["portable/helper.py"], final_revision=revision,
    )
    assert not result["passed"]
    assert any("changed after the final review" in blocker for blocker in result["blockers"])
