from portable.external_evaluation_campaign import (
    CampaignOutcome,
    CampaignRetestContract,
    ExternalEvaluationCampaign,
    LearningIntervention,
)


def campaign(**overrides):
    values = dict(
        campaign_id="external-1",
        campaign_version="1",
        arena_version="1",
        corpus_digest="c" * 64,
        generator_digest="g" * 64,
        oracle_digest="o" * 64,
        evaluator_version="eval-1",
        runtime_snapshot="runtime-1",
        case_ids=("a", "b", "c", "d"),
        holdout_case_ids=("c", "d"),
        novel_domain_case_ids=("c",),
        unfamiliar_tool_case_ids=("d",),
        external_attestation="attestation-1",
    )
    values.update(overrides)
    return ExternalEvaluationCampaign(**values)


def test_campaign_is_content_addressed_and_excludes_task_data():
    first = campaign()
    second = campaign()
    assert first.campaign_digest == second.campaign_digest
    payload = first.as_dict()
    assert "task" not in payload
    assert "answer" not in payload


def test_campaign_rejects_unknown_dimension_case():
    try:
        campaign(novel_domain_case_ids=("missing",))
    except ValueError as exc:
        assert "unknown case id" in str(exc)
    else:
        raise AssertionError("expected validation failure")


def test_contamination_or_missing_attestation_blocks_trust():
    assert not campaign(contamination_detected=True).trustworthy
    assert not campaign(external_attestation="").trustworthy


def test_outcome_requires_bounded_metrics():
    outcome = CampaignOutcome(
        campaign_digest=campaign().campaign_digest,
        holdout_pass_rate=0.8,
        holdout_verification_rate=0.8,
        generalization_gap=0.2,
        calibration_error=0.1,
        efficiency_score=0.7,
    )
    assert outcome.evidence_eligible


def test_retest_requires_fresh_independent_holdout():
    intervention = LearningIntervention(
        "i-1", "planner", "new planning policy helps", "improve holdout transfer",
        rollback_condition="regression on control group",
    )
    contract = CampaignRetestContract(campaign().campaign_digest, intervention.intervention_digest)
    assert contract.new_holdout_required
    try:
        CampaignRetestContract(
            campaign().campaign_digest, intervention.intervention_digest,
            new_holdout_required=False,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("expected fresh-holdout requirement")
