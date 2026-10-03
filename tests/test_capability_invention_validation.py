from portable.open_ended_capability_discovery import CapabilityInventionProposal
from portable.capability_invention_validation import CapabilityInventionValidator


def proposal():
    return CapabilityInventionProposal("p","adaptive-x","g","h","skill",
        ("novel-domain","unfamiliar-tool"),"rollback",("e1",))


def test_plan_is_fresh_and_independent():
    p=CapabilityInventionValidator().plan(proposal(),("planning","tools"))
    assert p.independent and p.fresh_holdout_required
    assert len(p.probes)==4
    assert all(x.holdout and x.oracle_required for x in p.probes)
    assert p.plan_digest


def test_requires_multiple_families():
    try:
        CapabilityInventionValidator().plan(proposal(),("planning",))
    except ValueError:
        pass
    else:
        raise AssertionError("expected diversity validation")
