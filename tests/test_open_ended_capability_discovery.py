from portable.open_ended_capability_discovery import CapabilityGap, OpenEndedCapabilityDiscovery


def test_discovers_ranked_falsifiable_proposal():
    gap=CapabilityGap("g1",("e1","e2"),("planning","tools"),("tool-selection","long-horizon"),.9,.8)
    p=OpenEndedCapabilityDiscovery().discover((gap,))[0]
    assert p.target_capability.startswith("adaptive-")
    assert "transfer gap" in p.hypothesis
    assert "fresh holdout" in p.rollback_condition
    assert p.proposal_digest


def test_deduplicates_and_budgets():
    a=CapabilityGap("a",("e",),("planning",),("x",),.4,.4)
    b=CapabilityGap("b",("e",),("tools",),("y",),.9,.9)
    out=OpenEndedCapabilityDiscovery().discover((a,b,a),budget=1)
    assert len(out)==1 and out[0].gap_id=="b"


def test_rejects_invalid_gap():
    try:
        CapabilityGap("g",(),("planning",),("x",),.5,.5)
    except ValueError:
        pass
    else:
        raise AssertionError("expected evidence validation")
