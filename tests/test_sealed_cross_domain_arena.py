from portable.sealed_arena_boundary import SealedCaseEnvelope, SealedCampaignRequest
from portable.sealed_cross_domain_arena import CrossDomainCampaign, SealedDomain


def request():
    return SealedCampaignRequest(
        "campaign", "corpus",
        (
            SealedCaseEnvelope("c1","t1","i1","e1",True),
            SealedCaseEnvelope("c2","t2","i2","e2",True),
            SealedCaseEnvelope("c3","t3","i3","e3",False),
        ),
    )


def test_requires_two_domains_and_holdout_per_domain():
    campaign = CrossDomainCampaign(
        "x", request(),
        (
            SealedDomain("math", ("c1",)),
            SealedDomain("code", ("c2","c3")),
        ),
        "oracle", "evaluator",
    )
    assert campaign.campaign_digest


def test_rejects_domain_without_holdout():
    try:
        CrossDomainCampaign(
            "x", request(),
            (
                SealedDomain("math", ("c1",)),
                SealedDomain("code", ("c3",)),
            ),
            "oracle", "evaluator",
        )
    except ValueError as exc:
        assert "holdout" in str(exc)
    else:
        raise AssertionError("expected holdout validation failure")


def test_rejects_conflated_evaluator_and_oracle():
    try:
        CrossDomainCampaign(
            "x", request(),
            (SealedDomain("math", ("c1",)), SealedDomain("code", ("c2",))),
            "same", "same",
        )
    except ValueError as exc:
        assert "distinct" in str(exc)
    else:
        raise AssertionError("expected principal separation failure")
