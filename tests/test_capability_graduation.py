from pathlib import Path
from tempfile import TemporaryDirectory
from portable.capability_graduation import CapabilityGraduationController
from portable.cross_task_capability_abstraction import CapabilityPattern, TransferValidation


def _pattern():
    return CapabilityPattern("p1","evidence-first","parallel","c|d|r|l|e|f",
                              ("verified-outcome",), "improve evidence", 6, .9, .9)


def _v(eid, uplift=.1, promoted=True, regression=True):
    return TransferValidation("p1", 2, .9, .8, uplift, regression, True, promoted, (eid,), "ok")


def test_requires_independent_cohorts_for_promotion():
    c=CapabilityGraduationController(minimum_cohorts=2)
    r=c.evaluate(_pattern(), [_v("a")])
    assert r.state == "canary"


def test_promotes_after_two_distinct_evidence_cohorts():
    c=CapabilityGraduationController(minimum_cohorts=2)
    r=c.evaluate(_pattern(), [_v("a"), _v("b")])
    assert r.state == "promoted"
    assert r.passing_cohorts == 2


def test_retires_after_repeated_verified_regressions():
    c=CapabilityGraduationController(minimum_cohorts=2)
    r=c.evaluate(_pattern(), [_v("a", -.1, False, False), _v("b", -.2, False, False)])
    assert r.state == "retired"
