from portable.causal_capability_promotion import (
    CausalCapabilityPromotionGate,
    CausalPromotionEvidence,
)


def evidence(**overrides):
    values = dict(
        capability_id="planner",
        intervention_id="i1",
        baseline_score=0.70,
        control_score=0.71,
        treatment_score=0.80,
        holdout_score=0.74,
        attribution_confidence=0.90,
        fresh_holdout=True,
        independent_oracle=True,
        contamination_detected=False,
        evidence_ids=("e1", "e2", "e3"),
    )
    values.update(overrides)
    return CausalPromotionEvidence(**values)


def test_gate_requires_both_control_lift_and_fresh_holdout_lift():
    decision = CausalCapabilityPromotionGate().evaluate(evidence())
    assert decision.eligible
    assert decision.state == "eligible"
    assert evidence().treatment_lift == 0.09


def test_regression_or_contamination_blocks():
    gate = CausalCapabilityPromotionGate()
    assert not gate.evaluate(evidence(contamination_detected=True)).eligible
    assert not gate.evaluate(evidence(control_score=0.80)).eligible


def test_missing_fresh_holdout_blocks():
    assert not CausalCapabilityPromotionGate().evaluate(
        evidence(fresh_holdout=False)
    ).eligible


def test_duplicate_evidence_is_rejected():
    try:
        evidence(evidence_ids=("e1", "e1"))
    except ValueError:
        pass
    else:
        raise AssertionError("expected duplicate evidence rejection")
