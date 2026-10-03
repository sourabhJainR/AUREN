from portable.campaign_intervention_loop import CampaignInterventionPlanner
from portable.external_evaluation_campaign import CampaignOutcome, LearningIntervention
from portable.adaptive_experiment_controller import ExperimentController, ExperimentReplication


def intervention():
    return LearningIntervention("i1", "planner", "hypothesis", "improve", "unchanged-policy", "regression")


def test_deterministic_assignment_and_no_leakage():
    i = intervention()
    c = ExperimentController()
    a = c.assign(experiment_id="e1", intervention=i, unit_id="u1", campaign_digest="c"*64, treatment=False)
    b = c.assign(experiment_id="e1", intervention=i, unit_id="u2", campaign_digest="c"*64, treatment=True)
    assert a.assignment_digest
    c.validate_assignments((a, b))
    try:
        c.validate_assignments((a, c.assign(experiment_id="e1", intervention=i, unit_id="u1", campaign_digest="d"*64, treatment=True)))
    except ValueError as exc:
        assert "leakage" in str(exc)
    else:
        raise AssertionError("expected leakage rejection")


def test_cross_campaign_replication_and_stop():
    i = intervention()
    prior = "p"*64
    contract = CampaignInterventionPlanner().retest_contract(prior, i)
    reps = tuple(
        ExperimentReplication(
            campaign_digest=chr(99+n)*64,
            domain=("planning" if n != 2 else "tools"),
            control_scores=(.50, .50),
            treatment_scores=(.57, .58),
            holdout_scores=(.53, .54),
            verified=True,
            independent_oracle=True,
            contamination_detected=False,
        )
        for n in range(3)
    )
    result = ExperimentController().aggregate(
        experiment_id="e1", intervention=i, replications=reps,
        retest_contract=contract, baseline_score=.50,
    )
    assert result.evidence_sufficient
    assert result.stop
    assert result.replication_count == 3


def test_insufficient_evidence_is_not_significance():
    i=intervention()
    contract=CampaignInterventionPlanner().retest_contract("p"*64, i)
    rep=ExperimentReplication("c"*64,"planning",(.5,),(.52,),(.51,),True,True,False)
    result=ExperimentController().aggregate(
        experiment_id="e",intervention=i,replications=(rep,),
        retest_contract=contract,baseline_score=.5)
    assert not result.evidence_sufficient
    assert "insufficient independent replications" in result.reasons


def test_regression_recommends_rollback():
    i=intervention()
    contract=CampaignInterventionPlanner().retest_contract("p"*64, i)
    reps=tuple(
        ExperimentReplication(chr(100+n)*64,"planning",(.6,.6),(.62,.62),(.50,.50),True,True,False)
        for n in range(3)
    )
    result=ExperimentController().aggregate(
        experiment_id="e",intervention=i,replications=reps,retest_contract=contract,baseline_score=.6)
    assert result.regression_detected
    assert result.rollback_recommended
    assert not result.evidence_sufficient
