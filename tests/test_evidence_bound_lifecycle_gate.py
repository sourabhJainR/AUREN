from portable.causal_capability_promotion import CausalCapabilityPromotionGate, CausalPromotionEvidence
from portable.capability_lifecycle import CapabilityLifecycleReceipt
from portable.evidence_bound_lifecycle_gate import EvidenceBoundLifecycleGate


def make_evidence():
    return CausalPromotionEvidence(
        "cap", "int", .5, .5, .6, .55, .9, True, True, False, ("e1", "e2")
    )


def test_requires_causal_and_canary_evidence():
    evidence = make_evidence()
    decision = CausalCapabilityPromotionGate().evaluate(evidence)
    lifecycle = CapabilityLifecycleReceipt("cap", "promoted", .5, 3, 3)
    result = EvidenceBoundLifecycleGate().evaluate(
        evidence=evidence, causal_decision=decision, lifecycle=lifecycle
    )
    assert result.eligible
    assert result.state == "promote"
    assert result.decision_digest


def test_blocks_rollback_or_insufficient_canary():
    evidence = make_evidence()
    decision = CausalCapabilityPromotionGate().evaluate(evidence)
    lifecycle = CapabilityLifecycleReceipt("cap", "rolled_back", .5, 3, 2)
    result = EvidenceBoundLifecycleGate().evaluate(
        evidence=evidence, causal_decision=decision, lifecycle=lifecycle
    )
    assert not result.eligible
    assert "rolled back" in " ".join(result.reasons)
    assert "not all canaries" in " ".join(result.reasons)


def test_blocks_identity_mismatch():
    evidence = make_evidence()
    decision = CausalCapabilityPromotionGate().evaluate(evidence)
    lifecycle = CapabilityLifecycleReceipt("other", "promoted", .5, 3, 3)
    result = EvidenceBoundLifecycleGate().evaluate(
        evidence=evidence, causal_decision=decision, lifecycle=lifecycle
    )
    assert not result.eligible
    assert "identity mismatch" in " ".join(result.reasons)
