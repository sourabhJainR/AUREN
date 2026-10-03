from portable.external_evaluation_campaign import CampaignOutcome
from portable.campaign_intervention_loop import CampaignInterventionPlanner, FailurePattern


def test_failures_become_falsifiable_interventions():
    outcome=CampaignOutcome("c"*64,.5,.5,.2,.1,.4,("case-1",))
    planner=CampaignInterventionPlanner()
    failures=(FailurePattern("planning",("e1","e2"),2,.6),)
    proposals=planner.propose(
        campaign_digest=outcome.campaign_digest,
        outcome=outcome,
        failures=failures,
        target_capability="planner",
    )
    assert len(proposals)==1
    assert "fresh holdout" in proposals[0].hypothesis
    contract=planner.retest_contract(outcome.campaign_digest,proposals[0])
    assert contract.new_holdout_required
    assert contract.independence_required


def test_mismatched_campaign_is_rejected():
    outcome=CampaignOutcome("c"*64,.5,.5,.2,.1,.4)
    try:
        CampaignInterventionPlanner().propose(
            campaign_digest="d"*64,outcome=outcome,
            failures=(FailurePattern("tool",("e",),1,.5),),
            target_capability="tools",
        )
    except ValueError as exc:
        assert "different campaign" in str(exc)
    else:
        raise AssertionError("expected campaign mismatch")
