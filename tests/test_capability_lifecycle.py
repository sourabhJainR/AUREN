from copy import deepcopy

from portable.capability_lifecycle import CapabilityLifecycle
from portable.persistent_memory import PersistentMemory

REPO = "owner/project"
BASE = "a" * 40
HEAD = "b" * 40
APPROVER = "release-manager"


def valid_record():
    return {
        "schema_version": "1.0",
        "task_id": "promotion-task",
        "repository": REPO,
        "base_commit": BASE,
        "head_commit": HEAD,
        "risk_level": "high",
        "acceptance_criteria": ["Canary suite passes without regression"],
        "selected_option": "Promote candidate behind reversible gate",
        "selection_rationale": "Meets the measured target with bounded blast radius",
        "alternatives": [{"option": "Immediate global rollout", "reason_rejected": "Not reversible"}],
        "dependencies": [],
        "checks": [{
            "name": "canary-regression",
            "command": "python -m unittest tests.test_capability_lifecycle",
            "status": "passed",
            "commit_sha": HEAD,
            "evidence_id": "run-123",
            "evaluator_type": "internal",
        }],
        "limitations": ["No independent generalization campaign is claimed"],
        "rollback_trigger": "Any post-promotion required check fails or safety regression is observed",
        "rollback_action": "Restore the last known-good capability version and rerun the holdout suite",
        "retry_history": [],
        "promotion": {
            "level": "verified_for_scope",
            "approved_by": APPROVER,
            "approval_evidence_id": "approval-1",
        },
    }


def lifecycle(tmp_path, record=None, **overrides):
    values = dict(
        min_canaries=3,
        regression_tolerance=0.02,
        promotion_record=record,
        expected_repository=REPO,
        expected_head=HEAD,
        authorized_approvers={APPROVER},
        known_evidence_ids={"run-123", "approval-1"},
    )
    values.update(overrides)
    return CapabilityLifecycle(
        PersistentMemory(tmp_path / "memory.db", require_approval=False), "p", **values
    )


def finish(lifecycle, capability_id):
    lifecycle.begin(capability_id, baseline_score=0.70)
    lifecycle.record_canary(capability_id, score=0.71, evidence_id=f"{capability_id}-1")
    lifecycle.record_canary(capability_id, score=0.73, evidence_id=f"{capability_id}-2")
    return lifecycle.record_canary(capability_id, score=0.72, evidence_id=f"{capability_id}-3")


def test_capability_promotes_only_with_valid_decision_record(tmp_path):
    gate = lifecycle(tmp_path, valid_record())
    final = finish(gate, "candidate-a")
    assert final.state == "promoted"
    assert final.canary_count == 3
    assert gate.status("candidate-a").state == "promoted"


def test_missing_record_or_trusted_context_blocks_promotion(tmp_path):
    blocked = finish(lifecycle(tmp_path / "missing", None), "candidate-missing")
    assert blocked.state == "promotion_blocked"
    assert any("decision record is required" in reason for reason in blocked.reasons)

    no_context = finish(
        lifecycle(tmp_path / "context", valid_record(), expected_head=None),
        "candidate-context",
    )
    assert no_context.state == "promotion_blocked"
    assert any("trusted repository and candidate-head" in reason for reason in no_context.reasons)


def test_stale_head_unknown_evidence_and_unauthorized_approver_block(tmp_path):
    stale = valid_record()
    stale["head_commit"] = "c" * 40
    result = finish(lifecycle(tmp_path / "stale", stale), "candidate-stale")
    assert result.state == "promotion_blocked"
    assert any("stale evidence" in reason for reason in result.reasons)

    unknown = valid_record()
    unknown["checks"][0]["evidence_id"] = "forged-run"
    result = finish(
        lifecycle(tmp_path / "unknown", unknown, known_evidence_ids={"approval-1"}),
        "candidate-unknown",
    )
    assert result.state == "promotion_blocked"
    assert any("unknown canonical evidence_id" in reason for reason in result.reasons)

    unauthorized = valid_record()
    unauthorized["promotion"]["approved_by"] = "untrusted-actor"
    result = finish(lifecycle(tmp_path / "unauthorized", unauthorized), "candidate-unauthorized")
    assert result.state == "promotion_blocked"
    assert any("authorized approver" in reason for reason in result.reasons)


def test_missing_rollback_action_blocks_before_promotion(tmp_path):
    record = valid_record()
    record["rollback_action"] = ""
    result = finish(lifecycle(tmp_path, record), "candidate-no-rollback")
    assert result.state == "promotion_blocked"
    assert any("rollback_action is required" in reason for reason in result.reasons)


def test_failed_recovery_rolls_back_even_with_valid_record(tmp_path):
    gate = lifecycle(tmp_path, valid_record())
    gate.begin("candidate-recovery", baseline_score=0.80)
    gate.record_canary("candidate-recovery", score=0.81, evidence_id="recovery-1")
    failed = gate.record_canary(
        "candidate-recovery", score=0.90, evidence_id="recovery-2", safe=False
    )
    assert failed.state == "rolled_back"
    assert gate.status("candidate-recovery").state == "rolled_back"


def test_duplicate_canary_evidence_is_rejected(tmp_path):
    gate = lifecycle(tmp_path)
    gate.begin("candidate-duplicate", baseline_score=0.80)
    gate.record_canary("candidate-duplicate", score=0.81, evidence_id="same")
    try:
        gate.record_canary("candidate-duplicate", score=0.82, evidence_id="same")
    except ValueError as exc:
        assert "already recorded" in str(exc)
    else:
        raise AssertionError("duplicate evidence must be rejected")
