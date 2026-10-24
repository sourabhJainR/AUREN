"""Tests for AUREN's evidence-driven evolution gates."""
import json
from pathlib import Path

import pytest

from portable.agency_evolution_gates import (
    ExperimentManifest,
    ImprovementHypothesis,
    ImprovementJournal,
    Signal,
    detect_drift,
    evaluate_promotion_gates,
    prioritize_signals,
)


def test_signal_priority_values_security_and_impact_over_frequency():
    frequent = Signal("freq", "latency", "slow requests", "trace:1", recurrence=1.0,
                      severity=0.2, expected_impact=0.2, security_relevance=0.0)
    security = Signal("security", "boundary", "missing authorization check", "trace:2",
                      recurrence=0.1, severity=0.9, expected_impact=0.8,
                      security_relevance=1.0)
    assert prioritize_signals([frequent, security])[0].signal_id == "security"


def test_hypothesis_requires_evidence_and_valid_state_transitions():
    hypothesis = ImprovementHypothesis(
        hypothesis_id="hyp-1", problem="repeated verification failure",
        evidence_refs=("trace:123",), proposed_change="add an invariant check",
        expected_benefit="fewer invalid releases", acceptance_criteria=("regression passes",),
        risk="low", budget_units=3, baseline_revision="base-sha",
        candidate_revision="candidate-sha", rollback_plan="revert candidate",
    )
    investigated = hypothesis.transition("investigated")
    proposed = investigated.transition("hypothesized")
    planned = proposed.transition("planned")
    evaluated = planned.transition("evaluated")
    with pytest.raises(ValueError, match="independent reviewer"):
        evaluated.transition("approved")
    approved = evaluated.transition("approved", reviewer_id="independent-reviewer", decision_ref="review:7")
    assert approved.state == "approved"
    with pytest.raises(ValueError, match="invalid hypothesis transition"):
        approved.transition("verified", reviewer_id="reviewer", decision_ref="decision")


def test_experiment_manifest_rejects_sealed_holdout_payload():
    with pytest.raises(ValueError, match="must never be embedded"):
        ExperimentManifest(
            experiment_id="exp-1", hypothesis_id="hyp-1", baseline_revision="base",
            candidate_revision="candidate", dataset_digest="dataset-sha",
            evaluator_revision="judge-v2", seed=7, budget_units=10,
            metrics=("quality",), holdout_reference="sealed-set-v1",
            holdout_payload_included=True,
        )


def test_fixed_baseline_detects_degradation_even_when_adaptive_baseline_moves():
    result = detect_drift(
        "quality", [0.78, 0.79, 0.80], fixed_baseline=0.9,
        adaptive_baseline=0.79, absolute_tolerance=0.05, adaptive_tolerance=0.05,
    )
    assert result.drift_detected
    assert result.reason == "fixed-release-baseline"
    assert result.absolute_delta < 0


def test_promotion_fails_closed_for_missing_gates_and_never_promotes_directly():
    decision = evaluate_promotion_gates(
        {"evaluator_independent": True, "holdout_isolated": True},
        candidate_passed=True, regression_passed=True, risk_approved=True,
    )
    assert decision.action == "block"
    assert "evidence_integrity" in decision.missing_gates
    ready = evaluate_promotion_gates(
        {
            "evaluator_independent": True, "holdout_isolated": True,
            "evidence_integrity": True, "required_checks_passed": True,
            "rollback_proven": True, "approval_recorded": True,
        },
        candidate_passed=True, regression_passed=True, risk_approved=True,
    )
    assert ready.action == "eligible_for_controller_approval"


def test_journal_is_hash_chained_idempotent_and_conflict_detecting(tmp_path: Path):
    path = tmp_path / "improvement.jsonl"
    journal = ImprovementJournal(path)
    first = journal.append(event_id="evt-1", work_id="work-1", state="observed",
                           payload={"source": "trace:1", "count": 2})
    duplicate = journal.append(event_id="evt-1", work_id="work-1", state="observed",
                               payload={"count": 2, "source": "trace:1"})
    assert duplicate == first
    second = journal.append(event_id="evt-2", work_id="work-1", state="investigated",
                            payload={"review": "independent"})
    assert [event.sequence for event in journal.read()] == [1, 2]
    assert second.previous_digest == first.digest
    with pytest.raises(ValueError, match="conflicting content"):
        journal.append(event_id="evt-1", work_id="work-1", state="deployed", payload={})


def test_journal_detects_tampering(tmp_path: Path):
    path = tmp_path / "improvement.jsonl"
    journal = ImprovementJournal(path)
    journal.append(event_id="evt-1", work_id="work-1", state="observed", payload={"score": 0.5})
    row = json.loads(path.read_text(encoding="utf-8"))
    row["payload"]["score"] = 0.99
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        journal.read()
